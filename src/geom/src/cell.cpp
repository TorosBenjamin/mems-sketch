#include "mgeom/cell.hpp"

#include <algorithm>
#include <cmath>
#include <set>

namespace mgeom {

namespace {

const Region kEmpty;

void check_child(const CellRef& cell) {
    if (!cell) throw GeometryError("cannot place a null cell");
}

Box merged(Box a, const Box& b) {
    if (b.empty()) return a;
    if (a.empty()) return b;
    return {std::min(a.x0, b.x0), std::min(a.y0, b.y0), std::max(a.x1, b.x1),
            std::max(a.y1, b.y1)};
}

// The box around a cell as placed by a transform: exact, from its regions.
Box placed_bbox(const Cell& cell, const Transform& t) {
    Box box;
    for (const std::string& layer : cell.layers()) {
        box = merged(box, cell.flat(layer).transformed(t).bbox());
    }
    return box;
}

}  // namespace

const Region& Cell::own(const std::string& layer) const {
    const auto it = own_.find(layer);
    return it == own_.end() ? kEmpty : it->second;
}

const Region& Cell::flat(const std::string& layer) const {
    std::lock_guard lock(cache_mutex_);
    auto& cached = flat_cache_[layer];
    if (!cached) {
        std::vector<Region> parts;
        if (const auto it = own_.find(layer); it != own_.end()) parts.push_back(it->second);
        for (const Placement& p : placements_) {
            const auto& child_layers = p.cell->layers();
            if (!std::binary_search(child_layers.begin(), child_layers.end(), layer)) continue;
            parts.push_back(p.cell->flat(layer).transformed(p.transform));
        }
        cached = std::make_shared<const Region>(Region::unite(parts));
    }
    return *cached;
}

Box Cell::bbox() const {
    Box box;
    for (const std::string& layer : layers_) box = merged(box, flat(layer).bbox());
    return box;
}

Cell::Builder::Builder(std::string name) : name_(std::move(name)) {}

Cell::Builder& Cell::Builder::add(const std::string& layer, const Region& region) {
    if (layer.empty()) throw GeometryError("a layer needs a name");
    if (!region.empty()) pending_[layer].push_back(region);
    return *this;
}

Cell::Builder& Cell::Builder::place(CellRef cell, const Transform& transform) {
    check_child(cell);
    placements_.push_back({cell, transform});
    references_.push_back({std::move(cell), transform, {}});
    return *this;
}

Cell::Builder& Cell::Builder::place_array(CellRef cell, const Transform& transform,
                                          const ArraySpec& array) {
    check_child(cell);
    if (array.columns < 1 || array.rows < 1) {
        throw GeometryError("an array needs at least one column and one row");
    }
    for (int j = 0; j < array.rows; ++j) {
        for (int i = 0; i < array.columns; ++i) {
            placements_.push_back(
                {cell, Transform::translation(i * array.dx, j * array.dy) * transform});
        }
    }
    references_.push_back({std::move(cell), transform, array});
    return *this;
}

Cell::Builder& Cell::Builder::place_polar(CellRef cell, const Transform& transform,
                                          const PolarSpec& polar) {
    check_child(cell);
    if (polar.count < 1) throw GeometryError("a polar array needs at least one copy");
    const double step = polar.step_deg == 0.0 ? 360.0 / polar.count : polar.step_deg;
    const Point c = polar.centre;
    Point own_centre = c;
    if (!polar.rotate) {
        const Box box = placed_bbox(*cell, transform);
        if (!box.empty()) own_centre = {(box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2};
    }
    for (int k = 0; k < polar.count; ++k) {
        const Transform turn = Transform::rotation(k * step);
        Transform around;
        if (polar.rotate) {
            around = Transform::translation(c.x, c.y) * turn * Transform::translation(-c.x, -c.y);
        } else {
            const Point turned = turn.apply({own_centre.x - c.x, own_centre.y - c.y});
            around = Transform::translation(c.x + turned.x - own_centre.x,
                                            c.y + turned.y - own_centre.y);
        }
        placements_.push_back({cell, around * transform});
        references_.push_back({cell, around * transform, {}});
    }
    return *this;
}

CellRef Cell::Builder::build() {
    auto cell = std::shared_ptr<Cell>(new Cell());
    cell->name_ = std::move(name_);
    std::set<std::string> layers;
    for (auto& [layer, regions] : pending_) {
        cell->own_[layer] = Region::unite(regions);
        layers.insert(layer);
    }
    for (const Placement& p : placements_) {
        layers.insert(p.cell->layers().begin(), p.cell->layers().end());
    }
    cell->placements_ = std::move(placements_);
    cell->references_ = std::move(references_);
    cell->layers_.assign(layers.begin(), layers.end());
    pending_.clear();
    placements_.clear();
    references_.clear();
    return cell;
}

}  // namespace mgeom
