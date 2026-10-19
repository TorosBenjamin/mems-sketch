// The centreline of a wire from port to port (see mgeom/route.hpp).
//
// A route is first its corners (the points where it changes direction), then
// those corners bent. A Manhattan route's corners come from a search over a
// small grid: the x and y of every point it must reach, of a stub out of each
// port, between them and just beyond them all. It goes along the grid lines,
// turning by 90° only where the runs before and after a bend have room for
// it; the fewest bends win, then the shortest, then the one whose bends are
// nearest the middle (a Z bends halfway).
#include "mgeom/route.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <numbers>
#include <queue>
#include <string>
#include <tuple>
#include <unordered_map>
#include <vector>

namespace mgeom {

namespace {

constexpr double kTolerance = 1e-9;  // µm: points this close are the same
constexpr double kAxis = 1e-6;       // degrees: a port this near x or y is along it

Point plus(Point a, Point b) { return {a.x + b.x, a.y + b.y}; }
Point minus(Point a, Point b) { return {a.x - b.x, a.y - b.y}; }
Point scaled(Point a, double s) { return {a.x * s, a.y * s}; }
double dot(Point a, Point b) { return a.x * b.x + a.y * b.y; }
double cross(Point a, Point b) { return a.x * b.y - a.y * b.x; }
double length(Point a) { return std::hypot(a.x, a.y); }
Point unit(Point a) { return scaled(a, 1 / length(a)); }

std::string number(double v) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%.4g", std::abs(v) < 5e-13 ? 0.0 : v);
    return buffer;
}

std::string at(Point p) { return "(" + number(p.x) + ", " + number(p.y) + ")"; }

// The unit vector of a direction, exact along x and y.
Point direction(double angle_deg) {
    const double quarter = angle_deg / 90;
    if (std::abs(quarter - std::round(quarter)) * 90 < kAxis) {
        static constexpr Point axes[] = {{1, 0}, {0, 1}, {-1, 0}, {0, -1}};
        return axes[((long(std::round(quarter)) % 4) + 4) % 4];
    }
    const double a = angle_deg * std::numbers::pi / 180;
    return {std::cos(a), std::sin(a)};
}

// -- the corners of each style ---------------------------------------------------

std::vector<Point> l_corners(const RouteEnd& start, const RouteEnd& end) {
    const Point d = direction(start.angle_deg), e = direction(end.angle_deg);
    const Point gap = minus(end.at, start.at);
    const double turn = cross(d, e);
    if (std::abs(turn) < kTolerance) {  // parallel: only straight across, facing
        if (dot(d, e) < 0 && std::abs(cross(d, gap)) < kTolerance && dot(d, gap) > kTolerance)
            return {start.at, end.at};
        throw GeometryError("an l route needs its ports' lines to meet, and these are parallel: use manhattan");
    }
    // start + s d = end + u e
    const double s = cross(gap, e) / turn, u = cross(gap, d) / turn;
    if (s <= kTolerance || u <= kTolerance)
        throw GeometryError("an l route bends where its ports' lines meet, and that is behind " +
                            std::string(s <= kTolerance ? "the start" : "the end") + ": use manhattan");
    return {start.at, plus(start.at, scaled(d, s)), end.at};
}

int axis_of(const RouteEnd& end, const char* which) {
    const double quarter = end.angle_deg / 90;
    if (std::abs(quarter - std::round(quarter)) * 90 >= kAxis)
        throw GeometryError(std::string("a manhattan route runs along x and y, and its ") + which + " faces " +
                            number(end.angle_deg) + "°: use straight or l");
    return int(((long(std::round(quarter)) % 4) + 4) % 4);
}

// Sorted, without near repeats.
std::vector<double> lines(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    std::vector<double> out;
    for (double v : values)
        if (out.empty() || v - out.back() > kTolerance) out.push_back(v);
    return out;
}

size_t line_of(const std::vector<double>& values, double v) {
    const auto found = std::lower_bound(values.begin(), values.end(), v - kTolerance);
    return size_t(found - values.begin());
}

struct Cost {
    int bends = 0;
    double length = 0.0, off_middle = 0.0;
    bool operator<(const Cost& o) const {
        return std::tie(bends, length, off_middle) < std::tie(o.bends, o.length, o.off_middle);
    }
    bool operator>(const Cost& o) const { return o < *this; }
};

// A Manhattan route's corners. ``end_run``: the least run between a port and
// a bend; ``turn_run``: between two bends.
std::vector<Point> manhattan_corners(const RouteEnd& start, const RouteEnd& end, std::span<const Point> via,
                                     double end_run, double turn_run) {
    static constexpr int dx[] = {1, 0, -1, 0}, dy[] = {0, 1, 0, -1};
    const int leave = axis_of(start, "start"), arrive = (axis_of(end, "end") + 2) % 4;
    const double stub = std::max(end_run, turn_run);
    std::vector<Point> stops{start.at};  // what it passes, in order
    stops.insert(stops.end(), via.begin(), via.end());
    stops.push_back(end.at);
    std::vector<double> xs, ys;
    for (size_t k = 0; k < stops.size(); ++k) {
        xs.push_back(stops[k].x), ys.push_back(stops[k].y);
        if (k > 0) xs.push_back((stops[k - 1].x + stops[k].x) / 2), ys.push_back((stops[k - 1].y + stops[k].y) / 2);
    }
    const Point out = plus(start.at, scaled(direction(start.angle_deg), stub));
    const Point in = plus(end.at, scaled(direction(end.angle_deg), stub));
    for (const Point p : {out, in, scaled(plus(out, in), 0.5)}) xs.push_back(p.x), ys.push_back(p.y);
    for (auto* values : {&xs, &ys}) {  // room to go round everything
        const auto [low, high] = std::minmax_element(values->begin(), values->end());
        const double below = *low - stub, above = *high + stub;
        values->push_back(below), values->push_back(above);
    }
    xs = lines(std::move(xs)), ys = lines(std::move(ys));
    const size_t nx = xs.size(), ny = ys.size(), legs = via.size() + 1;
    const auto node_at = [&](Point p) { return int(line_of(xs, p.x) * ny + line_of(ys, p.y)); };
    const auto point_of = [&](int n) { return Point{xs[size_t(n) / ny], ys[size_t(n) % ny]}; };
    std::vector<int> targets;
    for (size_t k = 1; k < stops.size(); ++k) targets.push_back(node_at(stops[k]));
    const int first = node_at(start.at), goal = targets.back();
    const Point middle = scaled(plus(start.at, end.at), 0.5);

    // A state: where, which way, how many stops are behind, and where the run
    // being made started (a node, or -1 once it is long enough for anything),
    // and whether that is the start (no bend yet).
    struct State {
        int node, dir, leg, run;
        bool from_start;
    };
    const auto key = [&](const State& s) {
        return ((((int64_t(s.node) * 4 + s.dir) * int64_t(legs + 1) + s.leg) * int64_t(nx * ny + 1) + (s.run + 1)) *
                2) + s.from_start;
    };
    const auto run_length = [&](const State& s, int node) {
        return s.run < 0 ? INFINITY : length(minus(point_of(node), point_of(s.run)));
    };
    const auto passed = [&](State s) {  // stops reached at its node
        while (s.leg < int(targets.size()) - 1 && targets[size_t(s.leg)] == s.node) ++s.leg;
        if (s.run >= 0 && run_length(s, s.node) >= std::max(end_run, turn_run) - kTolerance) s.run = -1;
        return s;
    };
    struct Seen {
        Cost cost;
        int64_t parent;
        State state;
    };
    std::unordered_map<int64_t, Seen> seen;
    using Entry = std::pair<Cost, int64_t>;
    std::priority_queue<Entry, std::vector<Entry>, std::greater<>> queue;
    const auto reach = [&](const State& s, Cost cost, int64_t parent) {
        const int64_t k = key(s);
        const auto found = seen.find(k);
        if (found != seen.end() && !(cost < found->second.cost)) return;
        seen[k] = {cost, parent, s};
        queue.push({cost, k});
    };
    reach(passed({first, leave, 0, first, true}), {}, -1);
    int64_t done = -1;
    while (!queue.empty()) {
        const auto [cost, k] = queue.top();
        queue.pop();
        const Seen here = seen.at(k);
        if (here.cost < cost) continue;  // reached more cheaply since
        const State s = here.state;
        if (s.node == goal && s.leg == int(targets.size()) - 1 && s.dir == arrive &&
            (s.from_start || run_length(s, s.node) >= end_run - kTolerance)) {
            done = k;
            break;
        }
        // straight on, to the next line
        const auto ix = size_t(s.node) / ny, iy = size_t(s.node) % ny;
        const long nix = long(ix) + dx[s.dir], niy = long(iy) + dy[s.dir];
        if (nix >= 0 && niy >= 0 && size_t(nix) < nx && size_t(niy) < ny) {
            const int next = int(size_t(nix) * ny + size_t(niy));
            Cost further = cost;
            further.length += length(minus(point_of(next), point_of(s.node)));
            reach(passed({next, s.dir, s.leg, s.run, s.from_start}), further, k);
        }
        // a bend, if the run so far has room for it
        if (run_length(s, s.node) >= (s.from_start ? end_run : turn_run) - kTolerance) {
            const Point p = point_of(s.node);
            Cost bent = cost;
            bent.bends += 1;
            bent.off_middle += std::abs(p.x - middle.x) + std::abs(p.y - middle.y);
            for (const int turn : {1, 3}) reach({s.node, (s.dir + turn) % 4, s.leg, s.node, false}, bent, k);
        }
    }
    if (done < 0)
        throw GeometryError("there is no manhattan route between these ports with bends this large (" +
                            number(turn_run) + " µm between bends, " + number(end_run) +
                            " µm from a port): try smaller bends, or via points");
    std::vector<Point> corners{end.at};
    for (int64_t k = done; k >= 0;) {
        const Seen& step = seen.at(k);
        if (step.parent >= 0 && seen.at(step.parent).state.node == step.state.node &&
            seen.at(step.parent).state.dir != step.state.dir)
            corners.push_back(point_of(step.state.node));  // a bend
        k = step.parent;
    }
    corners.push_back(start.at);
    std::reverse(corners.begin(), corners.end());
    return corners;
}

// -- bending the corners ----------------------------------------------------------

// The corners without repeats, and without those it goes straight through.
std::vector<Point> simplified(const std::vector<Point>& corners) {
    std::vector<Point> out;
    for (const Point p : corners)
        if (out.empty() || length(minus(p, out.back())) > kTolerance) out.push_back(p);
    for (size_t k = 1; k + 1 < out.size();) {
        const Point in = minus(out[k], out[k - 1]), next = minus(out[k + 1], out[k]);
        if (std::abs(cross(unit(in), unit(next))) < kTolerance && dot(in, next) > 0)
            out.erase(out.begin() + long(k));
        else
            ++k;
    }
    return out;
}

}  // namespace

Wire route(RouteEnd start, RouteEnd end, std::span<const Point> via, RouteStyle style, Bend bend, double radius,
           double width) {
    if (!(width > 0)) throw GeometryError("a route needs a positive width");
    if (bend == Bend::round && !(radius > width / 2))
        throw GeometryError("a round bend's radius must be more than half the width (" + number(width / 2) + " µm)");
    if (bend == Bend::chamfer && !(radius > 0)) throw GeometryError("a chamfered bend needs a positive radius");
    if (style == RouteStyle::l && !via.empty()) throw GeometryError("an l route has one bend and no via points");
    const double r = bend == Bend::sharp ? 0.0 : radius;  // along each side at a 90° bend
    const bool along_ports = style != RouteStyle::straight;
    std::vector<Point> corners;
    switch (style) {
        case RouteStyle::straight:
            corners.push_back(start.at);
            corners.insert(corners.end(), via.begin(), via.end());
            corners.push_back(end.at);
            break;
        case RouteStyle::l:
            corners = l_corners(start, end);
            break;
        case RouteStyle::manhattan:
            corners = manhattan_corners(start, end, via, std::max(r, width / 2), std::max(2 * r, width));
            break;
    }
    corners = simplified(corners);
    if (corners.size() < 2) throw GeometryError("a route's ports are at the same place " + at(start.at));

    // How far each bend reaches along its sides, and its turn (to the left > 0).
    const size_t n = corners.size();
    std::vector<double> reach(n, 0.0), turn(n, 0.0), per_radius(n, 0.0);
    for (size_t k = 1; k + 1 < n; ++k) {
        const Point in = unit(minus(corners[k], corners[k - 1])), out = unit(minus(corners[k + 1], corners[k]));
        turn[k] = std::atan2(cross(in, out), dot(in, out));
        if (std::abs(turn[k]) > std::numbers::pi - 1e-9)
            throw GeometryError("the route turns back on itself at " + at(corners[k]));
        per_radius[k] = bend == Bend::round ? std::tan(std::abs(turn[k]) / 2) : bend == Bend::chamfer ? 1.0 : 0.0;
        reach[k] = r * per_radius[k];
    }
    for (size_t k = 0; k + 1 < n; ++k) {
        const double side = length(minus(corners[k + 1], corners[k]));
        const bool at_port = k == 0 || k + 2 == n;
        if (along_ports && at_port && n > 2 && side < width / 2 - kTolerance)
            throw GeometryError("the route bends " + number(side) + " µm from a port, nearer than half its width (" +
                                number(width / 2) + " µm): it would cut back across the port");
        if (reach[k] + reach[k + 1] > side + kTolerance) {
            const double largest = side / (per_radius[k] + per_radius[k + 1]);
            throw GeometryError("bends of radius " + number(r) + " do not fit between " + at(corners[k]) + " and " +
                                at(corners[k + 1]) + ": the largest that fits is " + number(largest));
        }
    }

    Wire wire(corners.front());
    const auto line_to = [&](Point p) {
        if (length(minus(p, wire.end())) > kTolerance) wire.line_to(p);
    };
    for (size_t k = 1; k + 1 < n; ++k) {
        if (reach[k] <= 0) {
            line_to(corners[k]);
            continue;
        }
        const Point in = unit(minus(corners[k], corners[k - 1])), out = unit(minus(corners[k + 1], corners[k]));
        line_to(minus(corners[k], scaled(in, reach[k])));
        const Point after = plus(corners[k], scaled(out, reach[k]));
        if (bend == Bend::round)
            wire.arc_to(after, turn[k] > 0 ? r : -r);
        else
            wire.line_to(after);
    }
    line_to(corners.back());
    return wire;
}

}  // namespace mgeom
