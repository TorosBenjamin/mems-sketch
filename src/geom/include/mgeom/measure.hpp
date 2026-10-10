#pragma once
// Measurements on the exact geometry (requirements MEA-2 to MEA-4): for the
// Measure tool, the information panel and measurements in expressions.

#include <vector>

#include "mgeom/region.hpp"
#include "mgeom/types.hpp"

namespace mgeom {

// The properties of a region's faces. Lengths in µm, second moments of area
// in µm⁴, taken about the centroid.
struct Properties {
    double area = 0.0;       // µm²
    double perimeter = 0.0;  // µm, the boundaries of holes included
    Point centroid;
    double ix = 0.0;   // ∫ (y - ȳ)² dA: resists bending about the x axis
    double iy = 0.0;   // ∫ (x - x̄)² dA
    double ixy = 0.0;  // ∫ (x - x̄)(y - ȳ) dA
    Box bbox;

    double polar() const { return ix + iy; }  // ∫ r² dA about the centroid
};

Properties properties(const Region& region);

// A region as a prism of a layer's thickness and density, in SI units: what
// moves with a proof mass.
struct MassProperties {
    double volume = 0.0;  // m³
    double mass = 0.0;    // kg
    Point centroid;       // µm, as the region's
    double izz = 0.0;     // kg m², about the vertical axis through the centroid
};

MassProperties mass_properties(const Properties& properties, double thickness_um,
                               double density_kg_per_m3);

// The exact minimum distance between two regions, with the two points where
// it is reached. 0 when they touch or overlap. Requires both non-empty.
struct Distance {
    double value = 0.0;  // µm
    Point a;             // on the first region
    Point b;             // on the second region
};

Distance distance(const Region& a, const Region& b);

// The area two regions share (µm²).
double overlap_area(const Region& a, const Region& b);

// How far two regions overlap along a direction (angle_deg from the x
// axis): the length their projections onto that line share, in µm. For a
// comb drive, along the fingers it is their engagement.
double projected_overlap(const Region& a, const Region& b, double angle_deg);

// One edge of a region's boundary, in the order the boundary runs.
enum class EdgeKind { line, arc, curve };

struct Edge {
    EdgeKind kind = EdgeKind::line;
    Point start;
    Point end;
    Point mid;             // halfway along the edge
    double length = 0.0;   // µm
    Point centre;          // arcs only
    double radius = 0.0;   // arcs only
};

std::vector<Edge> edges(const Region& region);

}  // namespace mgeom
