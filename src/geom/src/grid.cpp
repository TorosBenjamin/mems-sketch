// Booleans and offsets on the grid, with Clipper2.
#include "mgeom/grid.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

#include <clipper2/clipper.h>

namespace mgeom::grid {

namespace {

using Clipper2Lib::Path64;
using Clipper2Lib::Paths64;
using Clipper2Lib::PolyPath64;
using Clipper2Lib::PolyTree64;

Path64 to_path(const GridRing& ring) {
    Path64 path;
    path.reserve(ring.size());
    for (const GridPoint& p : ring) path.emplace_back(p.x, p.y);
    return path;
}

GridRing from_path(const Path64& path, bool want_ccw) {
    GridRing ring;
    ring.reserve(path.size());
    for (const auto& p : path) ring.push_back({p.x, p.y});
    if ((Clipper2Lib::Area(path) > 0) != want_ccw) std::reverse(ring.begin(), ring.end());
    return ring;
}

// Hulls and holes as paths with the winding Clipper expects: hulls positive,
// holes negative, whatever way they were given.
Paths64 to_paths(const std::vector<GridPolygon>& polygons) {
    Paths64 paths;
    for (const GridPolygon& polygon : polygons) {
        Path64 hull = to_path(polygon.hull);
        if (Clipper2Lib::Area(hull) < 0) std::reverse(hull.begin(), hull.end());
        paths.push_back(std::move(hull));
        for (const GridRing& h : polygon.holes) {
            Path64 hole = to_path(h);
            if (Clipper2Lib::Area(hole) > 0) std::reverse(hole.begin(), hole.end());
            paths.push_back(std::move(hole));
        }
    }
    return paths;
}

void collect(const PolyPath64& node, std::vector<GridPolygon>& out) {
    for (const auto& outer : node) {
        GridPolygon polygon{from_path(outer->Polygon(), true), {}};
        for (const auto& hole : *outer) polygon.holes.push_back(from_path(hole->Polygon(), false));
        out.push_back(std::move(polygon));
        for (const auto& hole : *outer) collect(*hole, out);
    }
}

std::vector<GridPolygon> from_tree(const PolyTree64& tree) {
    std::vector<GridPolygon> out;
    collect(tree, out);
    return out;
}

}  // namespace

std::vector<GridPolygon> merged(const std::vector<GridPolygon>& polygons) {
    Clipper2Lib::Clipper64 clipper;
    clipper.AddSubject(to_paths(polygons));
    PolyTree64 tree;
    clipper.Execute(Clipper2Lib::ClipType::Union, Clipper2Lib::FillRule::NonZero, tree);
    return from_tree(tree);
}

std::vector<GridPolygon> boolean(const std::vector<GridPolygon>& a, const std::vector<GridPolygon>& b, Op op) {
    using Clipper2Lib::ClipType;
    const ClipType type = op == Op::unite       ? ClipType::Union
                          : op == Op::intersect ? ClipType::Intersection
                          : op == Op::subtract  ? ClipType::Difference
                                                : ClipType::Xor;
    Clipper2Lib::Clipper64 clipper;
    clipper.AddSubject(to_paths(a));
    clipper.AddClip(to_paths(b));
    PolyTree64 tree;
    clipper.Execute(type, Clipper2Lib::FillRule::NonZero, tree);
    return from_tree(tree);
}

std::vector<GridPolygon> offset(const std::vector<GridPolygon>& polygons, double delta, Join join) {
    using Clipper2Lib::JoinType;
    const JoinType type = join == Join::round ? JoinType::Round : join == Join::bevel ? JoinType::Bevel : JoinType::Miter;
    // Merged first, so that touching and overlapping polygons offset as one.
    const Paths64 paths = to_paths(merged(polygons));
    Clipper2Lib::ClipperOffset offsetter(/*miter_limit=*/2.0, /*arc_tolerance=*/0.5);
    offsetter.AddPaths(paths, type, Clipper2Lib::EndType::Polygon);
    PolyTree64 tree;
    offsetter.Execute(delta, tree);
    return from_tree(tree);
}

// -- holes joined to their hull ---------------------------------------------------
//
// The hole elimination of earcut (Mapbox, ISC licence): holes are taken in
// order of their leftmost point; a ray to the left from that point finds the
// hull edge it sees, and the hole is joined to the best visible vertex near
// there by a pair of coincident edges. Hulls run counter-clockwise, holes
// clockwise.

namespace {

// Exact for any 32-bit coordinates where the compiler has 128-bit integers;
// elsewhere (MSVC) exact up to about 9e15 per product, 95 mm squares in nm.
#ifdef __SIZEOF_INT128__
__extension__ using Wide = __int128;
#else
using Wide = long double;
#endif

struct Node {
    std::int64_t x, y;
    int prev = -1, next = -1;
};

class Rings {
public:
    int ring(const GridRing& points, bool ccw) {
        GridRing r = points;
        Wide twice = 0;
        for (size_t k = 0; k < r.size(); ++k) {
            const GridPoint& a = r[k];
            const GridPoint& b = r[(k + 1) % r.size()];
            twice += static_cast<Wide>(a.x) * b.y - static_cast<Wide>(b.x) * a.y;
        }
        if ((twice > 0) != ccw) std::reverse(r.begin(), r.end());
        const int first = static_cast<int>(nodes.size());
        for (size_t k = 0; k < r.size(); ++k) {
            nodes.push_back({r[k].x, r[k].y});
            Node& n = nodes.back();
            n.prev = first + static_cast<int>((k + r.size() - 1) % r.size());
            n.next = first + static_cast<int>((k + 1) % r.size());
        }
        return first;
    }

    // Twice the signed area of p, q, r, negated: < 0 for a left turn.
    Wide area(int p, int q, int r) const {
        const Node &a = nodes[p], &b = nodes[q], &c = nodes[r];
        return static_cast<Wide>(b.y - a.y) * (c.x - b.x) - static_cast<Wide>(b.x - a.x) * (c.y - b.y);
    }

    bool locally_inside(int a, int b) const {
        const Node& n = nodes[a];
        return area(n.prev, a, n.next) < 0 ? area(a, b, n.next) >= 0 && area(a, n.prev, b) >= 0
                                           : area(a, b, n.prev) < 0 || area(a, n.next, b) < 0;
    }

    bool sector_contains_sector(int m, int p) const {
        return area(nodes[m].prev, m, nodes[p].prev) < 0 && area(nodes[p].next, m, nodes[m].next) < 0;
    }

    static bool in_triangle(long double ax, long double ay, long double bx, long double by, long double cx,
                            long double cy, long double px, long double py) {
        return (cx - px) * (ay - py) >= (ax - px) * (cy - py) && (ax - px) * (by - py) >= (bx - px) * (ay - py) &&
               (bx - px) * (cy - py) >= (cx - px) * (by - py);
    }

    int bridge(int hole, int outer) const {
        const long double hx = nodes[hole].x, hy = nodes[hole].y;
        long double qx = -std::numeric_limits<long double>::infinity();
        int m = -1;
        int p = outer;
        do {
            const Node &a = nodes[p], &b = nodes[a.next];
            if (hy <= a.y && hy >= b.y && b.y != a.y) {
                const long double x = a.x + (hy - a.y) * static_cast<long double>(b.x - a.x) / (b.y - a.y);
                if (x <= hx && x > qx) {
                    qx = x;
                    m = a.x < b.x ? p : a.next;
                    if (x == hx) return m;  // the hole touches the edge
                }
            }
            p = a.next;
        } while (p != outer);
        if (m < 0) return -1;
        const int stop = m;
        const long double mx = nodes[m].x, my = nodes[m].y;
        long double tan_min = std::numeric_limits<long double>::infinity();
        p = m;
        do {
            const long double px = nodes[p].x, py = nodes[p].y;
            if (hx >= px && px >= mx && hx != px &&
                in_triangle(hy < my ? hx : qx, hy, mx, my, hy < my ? qx : hx, hy, px, py)) {
                const long double tan = std::fabs(hy - py) / (hx - px);
                if (locally_inside(p, hole) &&
                    (tan < tan_min ||
                     (tan == tan_min && (px > nodes[m].x || (px == nodes[m].x && sector_contains_sector(m, p)))))) {
                    m = p;
                    tan_min = tan;
                }
            }
            p = nodes[p].next;
        } while (p != stop);
        return m;
    }

    // Joins b's ring to a's by coincident edges a-b and b'-a'.
    void split(int a, int b) {
        const int a2 = static_cast<int>(nodes.size());
        nodes.push_back({nodes[a].x, nodes[a].y});
        const int b2 = static_cast<int>(nodes.size());
        nodes.push_back({nodes[b].x, nodes[b].y});
        const int an = nodes[a].next, bp = nodes[b].prev;
        nodes[a].next = b;
        nodes[b].prev = a;
        nodes[a2].next = an;
        nodes[an].prev = a2;
        nodes[b2].next = a2;
        nodes[a2].prev = b2;
        nodes[bp].next = b2;
        nodes[b2].prev = bp;
    }

    GridRing walk(int start) const {
        GridRing out;
        int p = start;
        do {
            const GridPoint q{nodes[p].x, nodes[p].y};
            if (out.empty() || !(out.back() == q)) out.push_back(q);
            p = nodes[p].next;
        } while (p != start);
        while (out.size() > 1 && out.front() == out.back()) out.pop_back();
        return out;
    }

    std::vector<Node> nodes;
};

GridRing joined(const GridPolygon& polygon) {
    Rings rings;
    const int outer = rings.ring(polygon.hull, true);
    std::vector<int> lefts;
    for (const GridRing& h : polygon.holes) {
        if (h.size() < 3) continue;
        const int first = rings.ring(h, false);
        int left = first;
        for (int p = rings.nodes[first].next; p != first; p = rings.nodes[p].next) {
            const Node &n = rings.nodes[p], &l = rings.nodes[left];
            if (n.x < l.x || (n.x == l.x && n.y < l.y)) left = p;
        }
        lefts.push_back(left);
    }
    std::sort(lefts.begin(), lefts.end(), [&](int a, int b) {
        const Node &na = rings.nodes[a], &nb = rings.nodes[b];
        return na.x != nb.x ? na.x < nb.x : na.y < nb.y;
    });
    for (const int hole : lefts) {
        const int b = rings.bridge(hole, outer);
        if (b < 0) throw std::runtime_error("a hole lies outside its polygon");
        rings.split(b, hole);
    }
    return rings.walk(outer);
}

size_t points_of(const GridPolygon& p) {
    size_t n = p.hull.size();
    for (const GridRing& h : p.holes) n += h.size() + 2;  // each cut repeats two points
    return n;
}

void hole_free_into(const GridPolygon& polygon, size_t max_points, int depth, std::vector<GridRing>& out) {
    if (points_of(polygon) <= max_points) {
        out.push_back(polygon.holes.empty() ? polygon.hull : joined(polygon));
        return;
    }
    if (depth > 60) throw std::runtime_error("a polygon could not be split into small enough pieces");
    std::int64_t x0 = polygon.hull[0].x, x1 = x0, y0 = polygon.hull[0].y, y1 = y0;
    for (const GridPoint& q : polygon.hull) {
        x0 = std::min(x0, q.x), x1 = std::max(x1, q.x), y0 = std::min(y0, q.y), y1 = std::max(y1, q.y);
    }
    const bool vertical = x1 - x0 >= y1 - y0;
    const std::int64_t cut = vertical ? x0 + (x1 - x0) / 2 : y0 + (y1 - y0) / 2;
    auto box = [](std::int64_t l, std::int64_t b, std::int64_t r, std::int64_t t) {
        return GridPolygon{{{l, b}, {r, b}, {r, t}, {l, t}}, {}};
    };
    const GridPolygon sides[2] = {vertical ? box(x0, y0, cut, y1) : box(x0, y0, x1, cut),
                                  vertical ? box(cut, y0, x1, y1) : box(x0, cut, x1, y1)};
    for (const GridPolygon& side : sides) {
        for (const GridPolygon& piece : boolean({polygon}, {side}, Op::intersect)) {
            hole_free_into(piece, max_points, depth + 1, out);
        }
    }
}

}  // namespace

std::vector<GridRing> hole_free(const std::vector<GridPolygon>& polygons, size_t max_points) {
    std::vector<GridRing> out;
    for (const GridPolygon& p : polygons) {
        if (p.hull.size() >= 3) hole_free_into(p, std::max<size_t>(max_points, 8), 0, out);
    }
    return out;
}

}  // namespace mgeom::grid
