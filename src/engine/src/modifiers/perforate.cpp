// Holes on a square or hexagonal grid, each wholly inside the node's outline
// shrunk by the margin and clear of the keep-out shapes, so there are no half
// holes (mems_sketch.core.shapes.modifiers.PerforateModifier). The grid is
// centred on the node unless x, y put a hole's centre somewhere; the holes
// refill when the node changes. One hole shape, placed at every site that
// fits and subtracted in one cut.
#include <cmath>
#include <cstdio>
#include <vector>

#include "mgeom/fit.hpp"
#include "modifiers.hpp"

namespace mems {

namespace {

constexpr double FIT_CHORD_UM = 1e-3;
// A hole is tested this much smaller all round, so one exactly the margin from
// the edge fits (the chords of the curves are within FIT_CHORD_UM).
constexpr double FIT_SLACK_UM = 2e-3;
constexpr size_t MAX_SITES = 2'000'000;

double positive(const Context& ctx, const Json& modifier, const char* key, double fallback, const char* what) {
    const double value = ctx.number(modifier, key, fallback);
    if (!(value > 0)) {
        char buffer[96];
        std::snprintf(buffer, sizeof buffer, "%s must be more than 0, got %g", what, value);
        throw BuildError(buffer);
    }
    return value;
}

bool given(const Json& modifier, const char* key) { return modifier.contains(key) && !modifier[key].is_null(); }

// Where the lattice's lines run along one axis inside [lo, hi]: through
// ``origin``, or (centred) through origin or half a step beside it, whichever
// gives more, so the holes sit symmetrically about the node's centre.
struct Lines {
    double first = 0.0;
    long long count = 0;
    long long index = 0;  // the index of ``first`` counted from origin's line
};

Lines lines(double lo, double hi, double origin, double step, bool centred) {
    const auto fit = [&](double through) {
        Lines l;
        if (hi < lo) return l;
        const double k0 = std::ceil((lo - through) / step - 1e-9), k1 = std::floor((hi - through) / step + 1e-9);
        l.index = static_cast<long long>(k0);
        l.count = std::max(0LL, static_cast<long long>(k1 - k0) + 1);
        l.first = through + k0 * step;
        return l;
    };
    const Lines on = fit(origin);
    if (!centred) return on;
    const Lines beside = fit(origin + step / 2.0);
    return beside.count > on.count ? beside : on;
}

mgeom::Region geometry_of(const Points& points) {
    if (!points.outline) throw BuildError("'" + points.name + "' has no geometry to keep holes away from");
    std::vector<mgeom::Region> regions;
    for (const auto& [layer, region] : flatten(points.outline->layers(), points.outline->instances()))
        regions.push_back(points.transform.is_identity() ? region : region.transformed(points.transform));
    return mgeom::Region::unite(regions);
}

}  // namespace

Result apply_perforate(const Json& modifier, const Produce& produce, const Variables& given_variables,
                       const ModifierContext& m) {
    const Result made = produce(given_variables);
    const Variables variables = with_self(modifier, made, given_variables);
    const Context ctx{m.builder, m.component, variables, m.scope};
    const bool hex = modifier.value("grid", std::string("square")) == "hex";
    const double pitch = positive(ctx, modifier, "pitch", 10.0, "perforation pitch");
    const double row_pitch = given(modifier, "row_pitch")
                                 ? positive(ctx, modifier, "row_pitch", pitch, "perforation row pitch")
                                 : (hex ? pitch * std::sqrt(3.0) / 2.0 : pitch);
    const double width = positive(ctx, modifier, "hole_size", 4.0, "hole size");
    const double height = given(modifier, "hole_height") ? positive(ctx, modifier, "hole_height", width, "hole height") : width;
    if (width >= pitch || height >= row_pitch)
        throw BuildError("the holes (" + number_text(width) + " x " + number_text(height) +
                         ") must be smaller than their pitch (" + number_text(pitch) + ", " + number_text(row_pitch) +
                         "): they would run into each other");
    const double radius = ctx.number(modifier, "corner_radius", std::min(width, height) / 2.0);
    if (radius < 0) throw BuildError("hole corner radius must not be negative");
    const double margin = ctx.number(modifier, "margin", 2.0);
    if (margin < 0) throw BuildError("perforation margin must not be negative");
    const double keep_margin = ctx.number(modifier, "keep_out_margin", margin);
    if (keep_margin < 0) throw BuildError("keep-out margin must not be negative");

    std::vector<mgeom::Region> keep;
    for (const auto& name : modifier.value("keep_out", Json::array())) {
        if (!name.is_string()) continue;
        const auto found = m.scope.find(name.get<std::string>());
        if (found == m.scope.end())
            throw BuildError("perforation keep-out: no shape named '" + name.get<std::string>() + "' is visible");
        keep.push_back(geometry_of(found->second));
    }
    mgeom::Region kept = mgeom::Region::unite(keep);
    if (!kept.empty() && keep_margin > 0) kept = kept.offset(keep_margin, mgeom::Join::round);

    const mgeom::Region hole = mgeom::Region::rounded_rect(-width / 2, -height / 2, width / 2, height / 2, radius);
    const double w = std::max(width / 2 - FIT_SLACK_UM, width / 4), h = std::max(height / 2 - FIT_SLACK_UM, height / 4);
    const mgeom::Region tested = mgeom::Region::rounded_rect(-w, -h, w, h, std::max(0.0, radius - FIT_SLACK_UM));
    const Layers flat = made.flat();
    const mgeom::Box whole = bbox_of(flat);
    const bool centred = !given(modifier, "x") && !given(modifier, "y");
    const double ox = ctx.number(modifier, "x", (whole.x0 + whole.x1) / 2.0);
    const double oy = ctx.number(modifier, "y", (whole.y0 + whole.y1) / 2.0);

    Layers result;
    for (const auto& [layer, region] : flat) {
        mgeom::Region area = margin > 0 ? region.offset(-margin, mgeom::Join::round) : region;
        if (!kept.empty()) area = area - kept;
        if (area.empty()) {
            result.emplace(layer, region);
            continue;
        }
        const mgeom::Box box = area.bbox();
        const Lines rows = lines(box.y0 + height / 2, box.y1 - height / 2, oy, row_pitch, centred);
        const Lines columns = lines(box.x0 + width / 2, box.x1 - width / 2, ox, pitch, centred);
        // Odd rows of a hex grid are half a pitch along, so one more may fit.
        const Lines shifted = hex ? lines(box.x0 + width / 2, box.x1 - width / 2, columns.first + pitch / 2, pitch, false)
                                  : columns;
        if (static_cast<double>(rows.count) * static_cast<double>(columns.count + 1) > MAX_SITES) {
            char buffer[128];
            std::snprintf(buffer, sizeof buffer,
                          "a perforation of more than %zu holes: make the pitch larger than %g", MAX_SITES, pitch);
            throw BuildError(buffer);
        }
        std::vector<mgeom::Point> sites;
        for (long long j = 0; j < rows.count; ++j) {
            const double y = rows.first + static_cast<double>(j) * row_pitch;
            const Lines& along = hex && ((rows.index + j) % 2 != 0) ? shifted : columns;
            for (long long i = 0; i < along.count; ++i) sites.push_back({along.first + static_cast<double>(i) * pitch, y});
        }
        const auto fits = mgeom::fits_inside(area, tested, sites, FIT_CHORD_UM);
        std::vector<mgeom::Region> holes;
        for (size_t k = 0; k < sites.size(); ++k)
            if (fits[k]) holes.push_back(hole.transformed(mgeom::Transform::translation(sites[k].x, sites[k].y)));
        result.emplace(layer, holes.empty() ? region : region - mgeom::Region::unite(holes));
    }
    return {result, made.points, {}, made.ports};
}

}  // namespace mems
