#pragma once
// Where a shape fits: for perforations, which places on a grid a hole can go
// without reaching an edge.

#include <span>
#include <vector>

#include "mgeom/region.hpp"
#include "mgeom/types.hpp"

namespace mgeom {

// For each site, whether shape moved there (by the site: shape is drawn
// around the origin) lies wholly inside area: nothing of it outside, and none
// of its edges crossing or touching area's boundary (an island of area's in
// it does not fit either). Curves are compared as chords no further than
// chord (µm) from them, so the answer is exact to that. Sites are tested
// against an index of area's edges: thousands of sites stay cheap.
std::vector<bool> fits_inside(const Region& area, const Region& shape, std::span<const Point> sites,
                              double chord = 1e-3);

}  // namespace mgeom
