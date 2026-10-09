// Expressions: a tokenizer, a recursive-descent parser for the part of Python's
// expression grammar mems-sketch allows, and an evaluator with Python's
// arithmetic (CPython's float floor division, modulo, power and round are
// followed step by step, so results agree to the last bit).
#include "mems/expression.hpp"

#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

namespace mems {

// -- the syntax tree ----------------------------------------------------------

struct Expression::Node {
    enum class Kind { number, name, attribute, unary, binary, call };
    Kind kind;
    Number number;           // number
    std::string id;          // name: the name; attribute: the attribute; call: unused
    char op = 0;             // unary: '+' '-'; binary: '+' '-' '*' '/' 'f' (//) '%' 'p' (**)
    std::vector<std::shared_ptr<const Node>> children;  // operands, value, or function + args
};

namespace {

using Node = Expression::Node;
using NodePtr = std::shared_ptr<const Node>;

[[noreturn]] void fail(const std::string& message) { throw ExpressionError(message); }

const std::set<std::string, std::less<>> FUNCTIONS = {
    "abs", "min", "max", "round", "sqrt", "sin", "cos", "tan", "ceil", "floor"};
const double PI = 3.141592653589793;

bool reserved(std::string_view name) { return FUNCTIONS.count(name) || name == "pi"; }

// Python's keywords cannot be names (True, None and False are constants that
// are not numbers: unsupported either way).
const std::set<std::string, std::less<>> KEYWORDS = {
    "False", "None",   "True",    "and",      "as",     "assert", "async", "await",
    "break", "class",  "continue", "def",     "del",    "elif",   "else",  "except",
    "finally", "for",  "from",    "global",   "if",     "import", "in",    "is",
    "lambda", "nonlocal", "not",  "or",       "pass",   "raise",  "return", "try",
    "while", "with",   "yield"};

// -- tokens -----------------------------------------------------------------

struct Token {
    enum class Kind { number, name, op, end };
    Kind kind;
    std::string text;  // name, operator ("+", "//", "**", "(", ")", ",", ".")
    Number number;
};

bool name_start(unsigned char c) { return std::isalpha(c) || c == '_' || c >= 0x80; }
bool name_char(unsigned char c) { return name_start(c) || std::isdigit(c); }

class Tokenizer {
public:
    explicit Tokenizer(std::string_view text) : s_(text) {}

    std::vector<Token> run() {
        std::vector<Token> tokens;
        int depth = 0;
        while (true) {
            skip_space(depth);
            if (i_ >= s_.size()) break;
            const unsigned char c = s_[i_];
            if (std::isdigit(c) || (c == '.' && i_ + 1 < s_.size() && std::isdigit((unsigned char)s_[i_ + 1]))) {
                tokens.push_back(number());
            } else if (name_start(c)) {
                const size_t start = i_;
                while (i_ < s_.size() && name_char(s_[i_])) ++i_;
                tokens.push_back({Token::Kind::name, std::string(s_.substr(start, i_ - start)), {}});
            } else {
                std::string op(1, static_cast<char>(c));
                if ((c == '*' || c == '/') && i_ + 1 < s_.size() && s_[i_ + 1] == static_cast<char>(c)) op += static_cast<char>(c);
                if (op != "+" && op != "-" && op != "*" && op != "/" && op != "//" && op != "%" &&
                    op != "**" && op != "(" && op != ")" && op != "," && op != ".")
                    fail("invalid syntax");
                if (op == "(") ++depth;
                if (op == ")" && --depth < 0) fail("unmatched ')'");
                i_ += op.size();
                tokens.push_back({Token::Kind::op, op, {}});
            }
        }
        if (depth > 0) fail("'(' was never closed");
        tokens.push_back({Token::Kind::end, "", {}});
        return tokens;
    }

private:
    void skip_space(int depth) {
        while (i_ < s_.size()) {
            const char c = s_[i_];
            if (c == ' ' || c == '\t' || c == '\f') {
                ++i_;
            } else if ((c == '\n' || c == '\r') && depth > 0) {
                ++i_;
            } else if (c == '#') {  // a comment, to the end of the line
                while (i_ < s_.size() && s_[i_] != '\n') ++i_;
            } else {
                return;
            }
        }
    }

    // digit (_? digit)*, for the given digits
    std::string digits(bool (*is_digit)(char)) {
        std::string out;
        if (i_ >= s_.size() || !is_digit(s_[i_])) fail("invalid number");
        out += s_[i_++];
        while (i_ < s_.size()) {
            if (s_[i_] == '_' && i_ + 1 < s_.size() && is_digit(s_[i_ + 1])) {
                ++i_;
            } else if (!is_digit(s_[i_])) {
                break;
            }
            out += s_[i_++];
        }
        return out;
    }

    static bool decimal(char c) { return c >= '0' && c <= '9'; }
    static bool hex(char c) { return std::isxdigit((unsigned char)c) != 0; }
    static bool octal(char c) { return c >= '0' && c <= '7'; }
    static bool binary(char c) { return c == '0' || c == '1'; }

    Token number() {
        Number value;
        const char next = i_ + 1 < s_.size() ? s_[i_ + 1] : '\0';
        if (s_[i_] == '0' && std::strchr("xXoObB", next) && next) {
            i_ += 2;
            if (i_ < s_.size() && s_[i_] == '_') ++i_;
            const int base = (next == 'x' || next == 'X') ? 16 : (next == 'o' || next == 'O') ? 8 : 2;
            const std::string d = digits(base == 16 ? hex : base == 8 ? octal : binary);
            double v = 0;
            for (char c : d) v = v * base + (std::isdigit((unsigned char)c) ? c - '0' : std::tolower(c) - 'a' + 10);
            value = Number::of_int(v);
        } else {
            std::string text;
            bool is_float = false;
            if (s_[i_] != '.') text = digits(decimal);
            if (i_ < s_.size() && s_[i_] == '.') {
                is_float = true;
                ++i_;
                text += '.';
                if (i_ < s_.size() && decimal(s_[i_])) text += digits(decimal);
            }
            if (i_ < s_.size() && (s_[i_] == 'e' || s_[i_] == 'E')) {
                const size_t mark = i_;
                std::string exponent = "e";
                ++i_;
                if (i_ < s_.size() && (s_[i_] == '+' || s_[i_] == '-')) exponent += s_[i_++];
                if (i_ < s_.size() && decimal(s_[i_])) {
                    text += exponent + digits(decimal);
                    is_float = true;
                } else {
                    i_ = mark;  // "1e" is a number followed by a name: an error below
                }
            }
            if (i_ < s_.size() && (s_[i_] == 'j' || s_[i_] == 'J')) fail("complex numbers are not supported");
            if (!is_float && text.size() > 1 && text[0] == '0' && text.find_first_not_of('0') != std::string::npos)
                fail("leading zeros in decimal integer literals are not permitted");
            const double v = std::strtod(text.c_str(), nullptr);
            value = is_float ? Number::of_float(v) : Number::of_int(v);
        }
        if (i_ < s_.size() && name_char(s_[i_])) fail("invalid decimal literal");
        return {Token::Kind::number, "", value};
    }

    std::string_view s_;
    size_t i_ = 0;
};

// -- parsing ----------------------------------------------------------------

class Parser {
public:
    explicit Parser(std::vector<Token> tokens) : t_(std::move(tokens)) {}

    NodePtr run() {
        NodePtr node = sum();
        if (peek().kind != Token::Kind::end) fail("invalid syntax");
        return node;
    }

private:
    const Token& peek() const { return t_[i_]; }
    bool is_op(const char* op) const { return peek().kind == Token::Kind::op && peek().text == op; }
    bool take(const char* op) {
        if (!is_op(op)) return false;
        ++i_;
        return true;
    }

    static NodePtr make(Node node) { return std::make_shared<const Node>(std::move(node)); }
    static NodePtr binary(char op, NodePtr left, NodePtr right) {
        return make({Node::Kind::binary, {}, {}, op, {std::move(left), std::move(right)}});
    }

    NodePtr sum() {
        NodePtr left = term();
        while (true) {
            if (take("+")) left = binary('+', left, term());
            else if (take("-")) left = binary('-', left, term());
            else return left;
        }
    }

    NodePtr term() {
        NodePtr left = factor();
        while (true) {
            if (take("*")) left = binary('*', left, factor());
            else if (take("/")) left = binary('/', left, factor());
            else if (take("//")) left = binary('f', left, factor());
            else if (take("%")) left = binary('%', left, factor());
            else return left;
        }
    }

    NodePtr factor() {
        for (const char* op : {"+", "-"}) {
            if (take(op)) return make({Node::Kind::unary, {}, {}, op[0], {factor()}});
        }
        return power();
    }

    NodePtr power() {
        NodePtr base = primary();
        if (take("**")) return binary('p', base, factor());  // right-associative, -x allowed
        return base;
    }

    NodePtr primary() {
        NodePtr node = atom();
        while (true) {
            if (take(".")) {
                if (peek().kind != Token::Kind::name || KEYWORDS.count(peek().text)) fail("invalid syntax");
                node = make({Node::Kind::attribute, {}, t_[i_++].text, 0, {node}});
            } else if (take("(")) {
                std::vector<NodePtr> children{node};
                while (!take(")")) {
                    children.push_back(sum());
                    if (!take(",") && !is_op(")")) fail("invalid syntax");
                }
                node = make({Node::Kind::call, {}, {}, 0, std::move(children)});
            } else {
                return node;
            }
        }
    }

    NodePtr atom() {
        const Token& token = peek();
        if (token.kind == Token::Kind::number) {
            ++i_;
            return make({Node::Kind::number, token.number, {}, 0, {}});
        }
        if (token.kind == Token::Kind::name) {
            if (KEYWORDS.count(token.text)) fail("unsupported syntax: '" + token.text + "'");
            ++i_;
            return make({Node::Kind::name, {}, token.text, 0, {}});
        }
        if (take("(")) {
            NodePtr inner = sum();
            if (!take(")")) fail("invalid syntax");
            return inner;
        }
        fail(token.kind == Token::Kind::end ? "unexpected end of expression" : "invalid syntax");
    }

    std::vector<Token> t_;
    size_t i_ = 0;
};

// ``a`` for a name, ``a.b.c`` for attributes on a name, else nothing.
std::optional<std::string> dotted(const Node& node) {
    if (node.kind == Node::Kind::name) return node.id;
    if (node.kind != Node::Kind::attribute) return std::nullopt;
    auto inner = dotted(*node.children[0]);
    if (!inner) return std::nullopt;
    return *inner + "." + node.id;
}

void collect_names(const Node& node, std::set<std::string>& names) {
    if (auto name = dotted(node)) {
        if (!reserved(*name)) names.insert(*name);
        return;
    }
    // A call's function is not a variable: only its arguments are visited.
    const size_t first = node.kind == Node::Kind::call ? 1 : 0;
    for (size_t k = first; k < node.children.size(); ++k) collect_names(*node.children[k], names);
}

// -- Python's arithmetic --------------------------------------------------------

constexpr double TWO_63 = 9223372036854775808.0;

bool fits_int64(double v) { return v > -TWO_63 && v < TWO_63; }

// a * b, or false when it does not fit in 64 bits.
bool multiply(int64_t a, int64_t b, int64_t& out) {
    constexpr int64_t hi = std::numeric_limits<int64_t>::max(), lo = std::numeric_limits<int64_t>::min();
    if (a == 0 || b == 0) {
        out = 0;
        return true;
    }
    const bool overflow = a > 0 ? (b > 0 ? a > hi / b : b < lo / a) : (b > 0 ? a < lo / b : b < hi / a);
    if (overflow) return false;
    out = a * b;
    return true;
}

bool is_odd_integer(double x) { return std::fmod(std::fabs(x), 2.0) == 1.0; }

// CPython's _float_div_mod.
void float_div_mod(double vx, double wx, double& floordiv, double& mod) {
    mod = std::fmod(vx, wx);
    double div = (vx - mod) / wx;
    if (mod != 0.0) {
        if ((wx < 0) != (mod < 0)) {
            mod += wx;
            div -= 1.0;
        }
    } else {
        mod = std::copysign(0.0, wx);
    }
    if (div != 0.0) {
        floordiv = std::floor(div);
        if (div - floordiv > 0.5) floordiv += 1.0;
    } else {
        floordiv = std::copysign(0.0, vx / wx);
    }
}

// CPython's float_pow.
double float_pow(double iv, double iw) {
    if (iw == 0) return 1.0;
    if (std::isnan(iv)) return iv;
    if (std::isnan(iw)) return iv == 1.0 ? 1.0 : iw;
    if (std::isinf(iw)) {
        iv = std::fabs(iv);
        if (iv == 1.0) return 1.0;
        if ((iw > 0.0) == (iv > 1.0)) return std::fabs(iw);
        return 0.0;
    }
    if (std::isinf(iv)) {
        const bool odd = is_odd_integer(iw);
        if (iw > 0.0) return odd ? iv : std::fabs(iv);
        return odd ? std::copysign(0.0, iv) : 0.0;
    }
    if (iv == 0.0) {
        if (iw < 0.0) fail("division by zero: zero to a negative power");
        return is_odd_integer(iw) ? iv : 0.0;
    }
    bool negate = false;
    if (iv < 0.0) {
        if (iw != std::floor(iw)) fail("a negative number to a fractional power is not a real number");
        iv = -iv;
        negate = is_odd_integer(iw);
    }
    if (iv == 1.0) return negate ? -1.0 : 1.0;
    double ix = std::pow(iv, iw);
    if (std::isinf(ix)) fail("result too large");
    return negate ? -ix : ix;
}

Number int_pow(double base, double exponent) {
    if (exponent < 0) return Number::of_float(float_pow(base, exponent));
    if (fits_int64(base) && fits_int64(exponent)) {  // exact, as Python's integers are
        int64_t b = static_cast<int64_t>(base), e = static_cast<int64_t>(exponent), result = 1;
        bool exact = true;
        while (e > 0 && exact) {
            if (e & 1) exact = multiply(result, b, result);
            e >>= 1;
            if (e > 0 && exact) exact = multiply(b, b, b);
        }
        if (exact) return Number::of_int(static_cast<double>(result));
    }
    return Number::of_int(std::pow(base, exponent));  // beyond 2^63: as near as a double gets
}

// An integer result; Python's would not convert to a float either.
Number checked(Number n) {
    if (n.integer && std::isinf(n.value)) fail("integer too large to convert to float");
    return n;
}

Number arithmetic(char op, Number a, Number b) {
    if (a.integer && b.integer && op != '/') {
        const double x = a.value, y = b.value;
        switch (op) {
        case '+': return checked(Number::of_int(x + y));
        case '-': return checked(Number::of_int(x - y));
        case '*': return checked(Number::of_int(x * y));
        case 'p': return checked(int_pow(x, y));
        case 'f':
        case '%': {
            if (y == 0) fail(op == 'f' ? "integer division by zero" : "integer modulo by zero");
            if (fits_int64(x) && fits_int64(y)) {
                const int64_t p = static_cast<int64_t>(x), q = static_cast<int64_t>(y);
                int64_t div = p / q, mod = p % q;
                if (mod != 0 && ((mod < 0) != (q < 0))) {
                    mod += q;
                    div -= 1;
                }
                return Number::of_int(static_cast<double>(op == 'f' ? div : mod));
            }
            double div, mod;
            float_div_mod(x, y, div, mod);
            return Number::of_int(op == 'f' ? div : mod);
        }
        }
    }
    const double x = a.value, y = b.value;
    switch (op) {
    case '+': return Number::of_float(x + y);
    case '-': return Number::of_float(x - y);
    case '*': return Number::of_float(x * y);
    case '/':
        if (y == 0) fail("division by zero");
        return Number::of_float(x / y);
    case 'f':
    case '%': {
        if (y == 0) fail(op == 'f' ? "float floor division by zero" : "float modulo by zero");
        if (op == '%') {
            double mod = std::fmod(x, y);
            if (mod != 0.0) {
                if ((y < 0) != (mod < 0)) mod += y;
            } else {
                mod = std::copysign(0.0, y);
            }
            return Number::of_float(mod);
        }
        double div, mod;
        float_div_mod(x, y, div, mod);
        return Number::of_float(div);
    }
    case 'p': return Number::of_float(float_pow(x, y));
    }
    fail("unsupported operator");
}

// A float rounded to an integer, as Python's int() of it.
Number to_int(double x) {
    if (std::isinf(x)) fail("cannot convert float infinity to integer");
    if (std::isnan(x)) fail("cannot convert float NaN to integer");
    return Number::of_int(x);
}

// round(x, ndigits) for a float: CPython's double_round, which rounds the exact
// decimal value of x half to even.
double round_float(double x, int64_t ndigits) {
    if (!std::isfinite(x)) return x;
    if (ndigits > 323) return x;
    if (ndigits < -308) return 0.0 * x;
    // Every double has a finite decimal expansion; 1100 places hold it exactly.
    std::vector<char> buffer(1500);
    const int length = std::snprintf(buffer.data(), buffer.size(), "%.1100f", std::fabs(x));
    std::string text(buffer.data(), static_cast<size_t>(length));
    const size_t point = text.find('.');
    std::string digits = text.substr(0, point) + text.substr(point + 1);
    const int64_t keep = static_cast<int64_t>(point) + ndigits;  // digits before the cut
    std::string kept = "0";
    if (keep >= 0) {
        kept = digits.substr(0, static_cast<size_t>(keep));
        const char first = static_cast<size_t>(keep) < digits.size() ? digits[keep] : '0';
        const bool rest = static_cast<size_t>(keep) + 1 < digits.size() &&
                          digits.find_first_not_of('0', keep + 1) != std::string::npos;
        const bool odd = !kept.empty() && ((kept.back() - '0') % 2 == 1);
        if (first > '5' || (first == '5' && (rest || odd))) {  // add one to the last kept digit
            size_t k = kept.size();
            while (k > 0 && kept[k - 1] == '9') kept[--k] = '0';
            if (k == 0) kept.insert(kept.begin(), '1');
            else ++kept[k - 1];
        }
        if (kept.empty()) kept = "0";
    }
    const std::string number = kept + "e" + std::to_string(-ndigits);
    const double rounded = std::strtod(number.c_str(), nullptr);
    if (std::isinf(rounded)) fail("rounded value too large to represent");
    return std::copysign(rounded, x);
}

// round(n, ndigits) for an integer: half to even at 10^-ndigits.
Number round_int(double n, int64_t ndigits) {
    if (ndigits >= 0) return Number::of_int(n);
    if (-ndigits > 18 || !fits_int64(n)) return Number::of_int(-ndigits > 18 ? 0.0 : n);
    int64_t p = 1;
    for (int64_t k = 0; k < -ndigits; ++k) p *= 10;
    const int64_t x = static_cast<int64_t>(n);
    int64_t q = x / p, r = x % p;
    if (r < 0) {
        r += p;
        q -= 1;
    }
    if (2 * r > p || (2 * r == p && (q % 2 != 0))) q += 1;
    return Number::of_int(static_cast<double>(q * p));
}

double math_1(const char* name, double x, double (*f)(double)) {
    const double r = f(x);
    if (std::isnan(r) && !std::isnan(x)) fail(std::string("math domain error (") + name + ")");
    if (std::isinf(r) && std::isfinite(x)) fail(std::string("math range error (") + name + ")");
    return r;
}

Number call(const std::string& name, const std::vector<Number>& args) {
    auto count = [&](size_t n) {
        if (args.size() != n)
            fail(name + "() takes " + std::to_string(n) + " argument" + (n == 1 ? "" : "s") + ", not " +
                 std::to_string(args.size()));
    };
    if (name == "min" || name == "max") {
        if (args.size() < 2) fail(name + "() needs at least two values");
        Number best = args[0];
        for (size_t k = 1; k < args.size(); ++k) {
            if (name == "min" ? args[k].value < best.value : args[k].value > best.value) best = args[k];
        }
        return best;
    }
    if (name == "round") {
        if (args.empty() || args.size() > 2)
            fail("round() takes 1 or 2 arguments, not " + std::to_string(args.size()));
        const Number x = args[0];
        if (args.size() == 1) {
            if (x.integer) return x;
            double rounded = std::round(x.value);
            if (std::fabs(x.value - rounded) == 0.5) rounded = 2.0 * std::round(x.value / 2.0);
            return to_int(rounded);
        }
        if (!args[1].integer) fail("round()'s number of digits must be an integer");
        const double digits = args[1].value;
        const int64_t ndigits = digits > 1e9 ? 1000000000 : digits < -1e9 ? -1000000000 : static_cast<int64_t>(digits);
        return x.integer ? round_int(x.value, ndigits) : Number::of_float(round_float(x.value, ndigits));
    }
    count(1);
    const Number x = args[0];
    if (name == "abs") return x.integer ? Number::of_int(std::fabs(x.value)) : Number::of_float(std::fabs(x.value));
    if (name == "ceil" || name == "floor") {
        if (x.integer) return x;
        return to_int(name == "ceil" ? std::ceil(x.value) : std::floor(x.value));
    }
    if (name == "sqrt") {
        if (x.value < 0) fail("math domain error (sqrt of a negative number)");
        return Number::of_float(std::sqrt(x.value));
    }
    if (name == "sin") return Number::of_float(math_1("sin", x.value, [](double v) { return std::sin(v); }));
    if (name == "cos") return Number::of_float(math_1("cos", x.value, [](double v) { return std::cos(v); }));
    if (name == "tan") return Number::of_float(math_1("tan", x.value, [](double v) { return std::tan(v); }));
    fail("unsupported function '" + name + "'");
}

Number eval(const Node& node, const Lookup& lookup) {
    switch (node.kind) {
    case Node::Kind::number: return node.number;
    case Node::Kind::name:
    case Node::Kind::attribute: {
        const auto name = dotted(node);
        if (!name) fail("unsupported syntax: an attribute of a value");
        if (auto value = lookup(*name)) return *value;  // variables come before constants
        if (*name == "pi") return Number::of_float(PI);
        fail("unknown variable '" + *name + "'");
    }
    case Node::Kind::unary: {
        const Number x = eval(*node.children[0], lookup);
        if (node.op == '+') return x;
        return x.integer ? Number::of_int(-x.value) : Number::of_float(-x.value);
    }
    case Node::Kind::binary: {
        const Number a = eval(*node.children[0], lookup);
        const Number b = eval(*node.children[1], lookup);
        return arithmetic(node.op, a, b);
    }
    case Node::Kind::call: {
        const Node& function = *node.children[0];
        if (function.kind != Node::Kind::name || !FUNCTIONS.count(function.id))
            fail("unsupported syntax: only " "abs, min, max, round, sqrt, sin, cos, tan, ceil and floor can be called");
        std::vector<Number> args;
        for (size_t k = 1; k < node.children.size(); ++k) args.push_back(eval(*node.children[k], lookup));
        return call(function.id, args);
    }
    }
    fail("unsupported syntax");
}

std::string_view strip(std::string_view text) {
    const char* space = " \t\n\r\f\v";
    const size_t first = text.find_first_not_of(space);
    if (first == std::string_view::npos) return {};
    return text.substr(first, text.find_last_not_of(space) - first + 1);
}

}  // namespace

// -- the public interface ------------------------------------------------------

Expression::Expression(std::string_view text) : text_(text) {
    try {
        root_ = Parser(Tokenizer(strip(text)).run()).run();
    } catch (const ExpressionError& error) {
        throw ExpressionError("invalid expression '" + text_ + "': " + error.what());
    }
    collect_names(*root_, names_);
}

Number Expression::evaluate_number(const Lookup& lookup) const { return eval(*root_, lookup); }

double Expression::evaluate(const Lookup& lookup) const { return evaluate_number(lookup).value; }

double Expression::evaluate(const Variables& variables) const {
    return evaluate([&](std::string_view name) -> std::optional<Number> {
        const auto found = variables.find(name);
        if (found == variables.end()) return std::nullopt;
        return Number::of_float(found->second);
    });
}

double evaluate(std::string_view text, const Variables& variables) {
    return Expression(text).evaluate(variables);
}

std::set<std::string> names_in(std::string_view text) { return Expression(text).names(); }

std::map<std::string, double> resolve_variables(
    const std::vector<std::pair<std::string, Definition>>& definitions, const Variables& fixed) {
    std::map<std::string, const Definition*> by_name;
    for (const auto& [name, definition] : definitions) by_name[name] = &definition;
    std::map<std::string, double> resolved;
    std::set<std::string> visiting;

    std::function<double(const std::string&)> visit = [&](const std::string& name) -> double {
        if (auto found = resolved.find(name); found != resolved.end()) return found->second;
        const auto definition = by_name.find(name);
        if (definition == by_name.end()) {
            if (auto known = fixed.find(name); known != fixed.end()) return known->second;
            throw ExpressionError("unknown variable '" + name + "'");
        }
        if (visiting.count(name)) throw ExpressionError("circular reference involving '" + name + "'");
        visiting.insert(name);
        double value;
        if (const auto* number = std::get_if<double>(definition->second)) {
            value = *number;
        } else {
            const Expression expression(std::get<std::string>(*definition->second));
            Variables needed;
            for (const auto& dependency : expression.names()) needed[dependency] = visit(dependency);
            value = expression.evaluate(needed);
        }
        visiting.erase(name);
        resolved[name] = value;
        return value;
    };
    for (const auto& [name, definition] : definitions) visit(name);
    return resolved;
}

}  // namespace mems
