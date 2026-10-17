// Which sites a shape fits at inside an area, on the outlines as polygons: no
// boolean per site. An edge index (a uniform grid of cells about the size of
// the shape) keeps each test to the edges near the site.

#include "mgeom/fit.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>

#include "mgeom/interrupt.hpp"

namespace mgeom {

namespace {

struct Edge {
    Point a, b;
};

double cross(Point o, Point a, Point b) { return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x); }

bool on_segment(Point a, Point b, Point p) {
    return std::min(a.x, b.x) <= p.x && p.x <= std::max(a.x, b.x) && std::min(a.y, b.y) <= p.y &&
           p.y <= std::max(a.y, b.y);
}

// Whether two segments cross or touch.
bool meet(const Edge& e, const Edge& f) {
    const double d1 = cross(f.a, f.b, e.a), d2 = cross(f.a, f.b, e.b);
    const double d3 = cross(e.a, e.b, f.a), d4 = cross(e.a, e.b, f.b);
    if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
    return (d1 == 0 && on_segment(f.a, f.b, e.a)) || (d2 == 0 && on_segment(f.a, f.b, e.b)) ||
           (d3 == 0 && on_segment(e.a, e.b, f.a)) || (d4 == 0 && on_segment(e.a, e.b, f.b));
}

Box box_of(const Edge& e) {
    return {std::min(e.a.x, e.b.x), std::min(e.a.y, e.b.y), std::max(e.a.x, e.b.x), std::max(e.a.y, e.b.y)};
}

bool overlap(const Box& a, const Box& b) { return a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1; }

// Even-odd: whether p is inside the rings the edges make (a horizontal ray to
// the right, crossing edges counted half-open in y).
template <typename Edges>
bool inside(const Edges& edges, Point p) {
    bool in = false;
    for (const Edge& e : edges) {
        if ((e.a.y > p.y) != (e.b.y > p.y)) {
            const double x = e.a.x + (p.y - e.a.y) * (e.b.x - e.a.x) / (e.b.y - e.a.y);
            if (x > p.x) in = !in;
        }
    }
    return in;
}

void add_ring(const Ring& ring, std::vector<Edge>& edges) {
    for (size_t i = 0, n = ring.size(); i < n; ++i) edges.push_back({ring[i], ring[(i + 1) % n]});
}

// A uniform grid of cells over a box, each listing the edges that reach it.
class EdgeGrid {
public:
    EdgeGrid(const std::vector<Edge>& edges, const Box& box, double cell) : edges_(edges), box_(box), cell_(cell) {
        nx_ = std::max<int64_t>(1, static_cast<int64_t>(std::ceil(box.width() / cell)));
        ny_ = std::max<int64_t>(1, static_cast<int64_t>(std::ceil(box.height() / cell)));
        cells_.resize(static_cast<size_t>(nx_ * ny_));
        rows_.resize(static_cast<size_t>(ny_));
        for (size_t k = 0; k < edges.size(); ++k) {
            const Box b = box_of(edges[k]);
            const auto [i0, j0] = cell_of({b.x0, b.y0});
            const auto [i1, j1] = cell_of({b.x1, b.y1});
            for (int64_t j = j0; j <= j1; ++j) {
                rows_[static_cast<size_t>(j)].push_back(k);
                for (int64_t i = i0; i <= i1; ++i) cells_[static_cast<size_t>(j * nx_ + i)].push_back(k);
            }
        }
        seen_.assign(edges.size(), 0);
    }

    // Whether an edge near box meets one of ``others``.
    bool meets(const Box& box, const std::vector<Edge>& others) {
        ++stamp_;
        const auto [i0, j0] = cell_of({box.x0, box.y0});
        const auto [i1, j1] = cell_of({box.x1, box.y1});
        for (int64_t j = j0; j <= j1; ++j) {
            for (int64_t i = i0; i <= i1; ++i) {
                for (const size_t k : cells_[static_cast<size_t>(j * nx_ + i)]) {
                    if (seen_[k] == stamp_) continue;
                    seen_[k] = stamp_;
                    const Edge& e = edges_[k];
                    if (!overlap(box_of(e), box)) continue;
                    for (const Edge& f : others)
                        if (meet(e, f)) return true;
                }
            }
        }
        return false;
    }

    // Whether p is inside the rings (only the edges of p's row of cells can
    // cross the ray: each is listed once per row).
    bool contains(Point p) const {
        if (p.x < box_.x0 || p.x > box_.x1 || p.y < box_.y0 || p.y > box_.y1) return false;
        bool in = false;
        for (const size_t k : rows_[static_cast<size_t>(cell_of(p).second)]) {
            const Edge& e = edges_[k];
            if ((e.a.y > p.y) != (e.b.y > p.y)) {
                const double x = e.a.x + (p.y - e.a.y) * (e.b.x - e.a.x) / (e.b.y - e.a.y);
                if (x > p.x) in = !in;
            }
        }
        return in;
    }

private:
    std::pair<int64_t, int64_t> cell_of(Point p) const {
        const auto clamp = [](double v, int64_t n) {
            return std::clamp<int64_t>(static_cast<int64_t>(std::floor(v)), 0, n - 1);
        };
        return {clamp((p.x - box_.x0) / cell_, nx_), clamp((p.y - box_.y0) / cell_, ny_)};
    }

    const std::vector<Edge>& edges_;
    Box box_;
    double cell_;
    int64_t nx_ = 1, ny_ = 1;
    std::vector<std::vector<size_t>> cells_, rows_;
    std::vector<uint32_t> seen_;
    uint32_t stamp_ = 0;
};

}  // namespace

std::vector<bool> fits_inside(const Region& area, const Region& shape, std::span<const Point> sites, double chord) {
    std::vector<bool> fits(sites.size(), false);
    if (area.empty() || shape.empty() || sites.empty()) return fits;

    std::vector<Edge> edges;  // area's boundary
    std::vector<Ring> rings;
    for (const Polygon& polygon : area.outlines(chord)) {
        add_ring(polygon.hull, edges);
        rings.push_back(polygon.hull);
        for (const Ring& hole : polygon.holes) add_ring(hole, edges), rings.push_back(hole);
    }
    std::vector<Edge> outline;  // shape's: only its outer boundaries matter
    for (const Polygon& polygon : shape.outlines(chord)) add_ring(polygon.hull, outline);
    if (outline.empty() || edges.empty()) return fits;
    const Box area_box = area.bbox(), shape_box = shape.bbox();

    const double size = std::max(shape_box.width(), shape_box.height());
    const double extent = std::max(area_box.width(), area_box.height());
    // Cells about the shape's size, but not more than 4096 a side.
    const double cell = std::max({size, extent / 4096.0, 1e-6});
    EdgeGrid grid(edges, area_box, cell);

    // area's rings, by their first point, to find islands inside a placed shape.
    std::vector<Box> ring_boxes;
    for (const Ring& ring : rings) {
        Box b;
        for (const Point& p : ring)
            b.x0 = std::min(b.x0, p.x), b.y0 = std::min(b.y0, p.y), b.x1 = std::max(b.x1, p.x),
            b.y1 = std::max(b.y1, p.y);
        ring_boxes.push_back(b);
    }

    std::vector<Edge> moved(outline.size());
    for (size_t s = 0; s < sites.size(); ++s) {
        if (s % 1024 == 0) check_interrupted();
        const Point at = sites[s];
        const Box box{shape_box.x0 + at.x, shape_box.y0 + at.y, shape_box.x1 + at.x, shape_box.y1 + at.y};
        if (box.x0 <= area_box.x0 || box.y0 <= area_box.y0 || box.x1 >= area_box.x1 || box.y1 >= area_box.y1) continue;
        for (size_t k = 0; k < outline.size(); ++k)
            moved[k] = {{outline[k].a.x + at.x, outline[k].a.y + at.y}, {outline[k].b.x + at.x, outline[k].b.y + at.y}};
        if (grid.meets(box, moved)) continue;     // an edge crosses: partly outside
        if (!grid.contains(moved[0].a)) continue;  // wholly outside (or in a hole of area)
        bool island = false;                      // a ring of area's wholly inside the shape
        for (size_t r = 0; r < rings.size() && !island; ++r) {
            const Box& b = ring_boxes[r];
            if (b.x0 > box.x0 && b.y0 > box.y0 && b.x1 < box.x1 && b.y1 < box.y1) island = inside(moved, rings[r][0]);
        }
        fits[s] = !island;
    }
    return fits;
}

}  // namespace mgeom
