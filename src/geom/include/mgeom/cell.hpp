#pragma once

#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "mgeom/region.hpp"
#include "mgeom/transform.hpp"

namespace mgeom {

class Cell;
using CellRef = std::shared_ptr<const Cell>;

// columns x rows copies, dx and dy apart in the parent's frame. The copy at
// column i, row j is moved by (i dx, j dy) after its placement.
struct ArraySpec {
    int columns = 1;
    int rows = 1;
    double dx = 0.0;
    double dy = 0.0;
};

// count copies around a centre, step_deg apart (0: a full circle, 360/count).
// With rotate the copies turn with the circle; without, only the centre of
// each copy's bounding box goes round and the copies keep their orientation.
struct PolarSpec {
    int count = 1;
    Point centre;
    double step_deg = 0.0;
    bool rotate = true;
};

// A reusable piece of layout: regions per layer, plus other cells placed in
// it. Placing a cell never copies its geometry: a copy is the cell plus a
// transform, so a cell placed 10,000 times is built once.
//
// A cell is immutable once built (by a Builder), so what it flattens to is
// computed once per layer and cached, safely across threads. That also
// rules out cycles: a cell can only place cells that already exist.
class Cell {
public:
    class Builder;

    struct Placement {
        CellRef cell;
        Transform transform;
    };

    // A placement as it was made: one copy, or a whole array of copies
    // (columns x rows, each moved by (i dx, j dy) after the transform). For
    // outputs that keep arrays as arrays, such as GDS array references. A
    // polar array is one reference per copy.
    struct Reference {
        CellRef cell;
        Transform transform;
        ArraySpec array;  // 1 x 1 for a single copy

        bool is_array() const { return array.columns > 1 || array.rows > 1; }
    };

    const std::string& name() const { return name_; }

    // The layers with geometry, its own or its placed cells', sorted.
    const std::vector<std::string>& layers() const { return layers_; }

    // The cell's own region on a layer (empty if none), without placements.
    const Region& own(const std::string& layer) const;

    // The cells placed directly in this one, one entry per copy (arrays
    // expanded), in the order they were placed.
    const std::vector<Placement>& placements() const { return placements_; }

    // The same copies, grouped as they were placed.
    const std::vector<Reference>& references() const { return references_; }

    // Everything on a layer, placed cells included, merged into one region.
    // Computed once and cached.
    const Region& flat(const std::string& layer) const;

    // The box around everything in the cell, on every layer.
    Box bbox() const;

private:
    Cell() = default;

    std::string name_;
    std::map<std::string, Region> own_;
    std::vector<Placement> placements_;
    std::vector<Reference> references_;
    std::vector<std::string> layers_;

    mutable std::mutex cache_mutex_;
    mutable std::map<std::string, std::shared_ptr<const Region>> flat_cache_;
};

class Cell::Builder {
public:
    explicit Builder(std::string name);

    // Add a region to a layer; it is merged with what is already there.
    Builder& add(const std::string& layer, const Region& region);
    Builder& place(CellRef cell, const Transform& transform = {});
    Builder& place_array(CellRef cell, const Transform& transform, const ArraySpec& array);
    Builder& place_polar(CellRef cell, const Transform& transform, const PolarSpec& polar);

    // The cell. The builder is left empty.
    CellRef build();

private:
    std::string name_;
    std::map<std::string, std::vector<Region>> pending_;
    std::vector<Placement> placements_;
    std::vector<Reference> references_;
};

}  // namespace mgeom
