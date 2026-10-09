// Snapping to a grid. Each piece of the region is split at the chord
// tolerance, rounded to the grid and cleaned up on its own, which tells
// which pieces vanished or split and which holes closed; then all pieces are
// united, which tells which ones merged. Clipper2 does the integer
// polygon work: it is exact on integers, so the result is deterministic.

#include "mgeom/snap.hpp"

#include <clipper2/clipper.h>

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

namespace mgeom {

namespace {

using Clipper2Lib::Path64;
using Clipper2Lib::Paths64;
using Clipper2Lib::PolyPath64;
using Clipper2Lib::PolyTree64;

constexpr double kMaxCoord = static_cast<double>(Clipper2Lib::MAX_COORD);

// Points are scaled by 1 / grid, as KLayout does, so a point exactly
// halfway between two grid points rounds the same way in both.
Path64 to_grid(const Ring& ring, double grid, bool want_ccw) {
    const double scale = 1.0 / grid;
    Path64 path;
    path.reserve(ring.size());
    for (const Point& p : ring) {
        const double x = std::round(p.x * scale);
        const double y = std::round(p.y * scale);
        if (std::abs(x) > kMaxCoord || std::abs(y) > kMaxCoord) {
            throw GeometryError("the geometry is too large for a grid of " +
                                std::to_string(grid) + " µm");
        }
        path.emplace_back(static_cast<std::int64_t>(x), static_cast<std::int64_t>(y));
    }
    if ((Clipper2Lib::Area(path) > 0) != want_ccw) std::reverse(path.begin(), path.end());
    return path;
}

GridRing from_path(const Path64& path, bool want_ccw) {
    GridRing ring;
    ring.reserve(path.size());
    for (const auto& p : path) ring.push_back({p.x, p.y});
    if ((Clipper2Lib::Area(path) > 0) != want_ccw) std::reverse(ring.begin(), ring.end());
    return ring;
}

// The outer polygons under a node of a polygon tree, with their holes;
// islands inside holes become polygons of their own.
void collect(const PolyPath64& node, std::vector<GridPolygon>& out) {
    for (const auto& outer : node) {
        GridPolygon polygon{from_path(outer->Polygon(), true), {}};
        for (const auto& hole : *outer) polygon.holes.push_back(from_path(hole->Polygon(), false));
        out.push_back(std::move(polygon));
        for (const auto& hole : *outer) collect(*hole, out);
    }
}

// Rings (hulls counter-clockwise, holes clockwise) as clean polygons:
// only what is inside with a positive winding is kept, so loops that
// rounding turned inside out disappear.
std::vector<GridPolygon> clean(const Paths64& paths) {
    Clipper2Lib::Clipper64 clipper;
    clipper.AddSubject(paths);
    PolyTree64 tree;
    clipper.Execute(Clipper2Lib::ClipType::Union, Clipper2Lib::FillRule::Positive, tree);
    std::vector<GridPolygon> out;
    collect(tree, out);
    return out;
}

Path64 to_path(const GridRing& ring) {
    Path64 path;
    path.reserve(ring.size());
    for (const GridPoint& p : ring) path.emplace_back(p.x, p.y);
    return path;
}

void add_paths(const GridPolygon& polygon, Paths64& paths) {
    paths.push_back(to_path(polygon.hull));
    for (const GridRing& h : polygon.holes) paths.push_back(to_path(h));
}

Box box_of(const Ring& ring) {
    Box b;
    for (const Point& p : ring) {
        b.x0 = std::min(b.x0, p.x);
        b.y0 = std::min(b.y0, p.y);
        b.x1 = std::max(b.x1, p.x);
        b.y1 = std::max(b.y1, p.y);
    }
    return b;
}

struct GridBox {
    std::int64_t x0 = std::numeric_limits<std::int64_t>::max();
    std::int64_t y0 = std::numeric_limits<std::int64_t>::max();
    std::int64_t x1 = std::numeric_limits<std::int64_t>::min();
    std::int64_t y1 = std::numeric_limits<std::int64_t>::min();

    bool contains(GridPoint p) const { return p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1; }
    Box in_um(double grid) const {
        return {static_cast<double>(x0) * grid, static_cast<double>(y0) * grid,
                static_cast<double>(x1) * grid, static_cast<double>(y1) * grid};
    }
};

GridBox box_of(const GridRing& ring) {
    GridBox b;
    for (const GridPoint& p : ring) {
        b.x0 = std::min(b.x0, p.x);
        b.y0 = std::min(b.y0, p.y);
        b.x1 = std::max(b.x1, p.x);
        b.y1 = std::max(b.y1, p.y);
    }
    return b;
}

double area_of(const GridPolygon& polygon) {
    double a = Clipper2Lib::Area(to_path(polygon.hull));
    for (const GridRing& h : polygon.holes) a += Clipper2Lib::Area(to_path(h));
    return a;
}

size_t hole_count(const std::vector<GridPolygon>& polygons) {
    size_t n = 0;
    for (const GridPolygon& p : polygons) n += p.holes.size();
    return n;
}

}  // namespace

Snapped snap(const Region& region, double grid, double chord) {
    if (!(grid > 0.0) || !std::isfinite(grid)) throw GeometryError("a grid needs a positive step");
    if (!(chord > 0.0) || !std::isfinite(chord)) {
        throw GeometryError("snapping needs a positive chord tolerance");
    }
    Snapped out;
    out.grid = grid;
    out.report.area_exact = region.area();
    auto& events = out.report.events;

    // Each piece on its own.
    std::vector<GridPolygon> parts;
    for (const Polygon& piece : region.outlines(chord)) {
        Paths64 paths{to_grid(piece.hull, grid, true)};
        for (const Ring& h : piece.holes) paths.push_back(to_grid(h, grid, false));
        std::vector<GridPolygon> snapped = clean(paths);
        const Box where = box_of(piece.hull);
        if (snapped.empty()) {
            events.push_back({SnapChange::vanished, where});
            continue;
        }
        if (snapped.size() > 1) events.push_back({SnapChange::split, where});
        size_t kept = 0;
        for (const Ring& h : piece.holes) {
            if (clean({to_grid(h, grid, true)}).empty()) {
                events.push_back({SnapChange::hole_closed, box_of(h)});
            } else {
                ++kept;
            }
        }
        const size_t after = hole_count(snapped);
        if (after < kept) events.push_back({SnapChange::hole_joined, where});
        if (after > kept) events.push_back({SnapChange::hole_formed, where});
        for (GridPolygon& p : snapped) parts.push_back(std::move(p));
    }

    // All pieces together: fewer polygons than parts means some merged.
    Paths64 all;
    for (const GridPolygon& p : parts) add_paths(p, all);
    out.polygons = clean(all);
    if (out.polygons.size() < parts.size()) {
        // Which parts went into which polygon: the smallest polygon whose
        // hull holds a part's first point (islands sit inside their
        // surrounding polygon's hull too).
        std::vector<GridBox> boxes;
        std::vector<double> areas;
        for (const GridPolygon& p : out.polygons) {
            boxes.push_back(box_of(p.hull));
            areas.push_back(Clipper2Lib::Area(to_path(p.hull)));
        }
        std::vector<int> count(out.polygons.size(), 0);
        for (const GridPolygon& part : parts) {
            const GridPoint q = part.hull.front();
            size_t best = out.polygons.size();
            for (size_t k = 0; k < out.polygons.size(); ++k) {
                if (!boxes[k].contains(q)) continue;
                if (best < out.polygons.size() && areas[k] >= areas[best]) continue;
                const auto inside = Clipper2Lib::PointInPolygon(
                    Clipper2Lib::Point64(q.x, q.y), to_path(out.polygons[k].hull));
                if (inside != Clipper2Lib::PointInPolygonResult::IsOutside) best = k;
            }
            if (best < out.polygons.size()) ++count[best];
        }
        for (size_t k = 0; k < out.polygons.size(); ++k) {
            if (count[k] > 1) events.push_back({SnapChange::merged, boxes[k].in_um(grid)});
        }
    }

    double area = 0.0;
    for (const GridPolygon& p : out.polygons) area += area_of(p);
    out.report.area_snapped = area * grid * grid;
    return out;
}

}  // namespace mgeom
