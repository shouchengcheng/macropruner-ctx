"""Unit tests for skeletonizer.py"""

import sys

sys.path.insert(0, ".")

from skeletonizer import Skeletonizer, skeletonize_source


def test_simple_function():
    source = """\
int add(int a, int b) {
    return a + b;
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "{ /* ... */ }" in result
    assert "return a + b" not in result
    assert "int add(int a, int b)" in result
    print("TEST simple_function: PASS")


def test_struct_preserved():
    source = """\
struct Point {
    int x;
    int y;
};

void draw(struct Point p) {
    printf("%d %d", p.x, p.y);
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "int x;" in result
    assert "int y;" in result
    assert "printf" not in result
    assert "void draw(struct Point p)" in result
    print("TEST struct_preserved: PASS")


def test_preprocessor_preserved():
    source = """\
#include <stdio.h>
#define MAX_SIZE 100

void init(void) {
    int arr[MAX_SIZE];
    memset(arr, 0, sizeof(arr));
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "#include <stdio.h>" in result
    assert "#define MAX_SIZE 100" in result
    assert "memset" not in result
    print("TEST preprocessor_preserved: PASS")


def test_multiple_functions():
    source = """\
int foo(int x) {
    return x * 2;
}

int bar(int y) {
    int z = y + 1;
    return z;
}

void baz(void) {
    foo(1);
    bar(2);
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert result.count("{ /* ... */ }") == 3
    assert "return x * 2" not in result
    assert "int z = y + 1" not in result
    assert "foo(1)" not in result
    stats = skel.get_stats()
    assert stats["functions_stripped"] == 3
    print("TEST multiple_functions: PASS")


def test_nested_braces_in_body():
    source = """\
void process(int n) {
    for (int i = 0; i < n; i++) {
        if (i % 2 == 0) {
            printf("%d\\n", i);
        }
    }
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "{ /* ... */ }" in result
    assert "for" not in result
    assert "printf" not in result
    assert result.count("{ /* ... */ }") == 1
    print("TEST nested_braces_in_body: PASS")


def test_enum_preserved():
    source = """\
enum Color {
    RED,
    GREEN,
    BLUE
};

void set_color(enum Color c) {
    current_color = c;
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "RED" in result
    assert "GREEN" in result
    assert "BLUE" in result
    assert "current_color = c" not in result
    print("TEST enum_preserved: PASS")


def test_typedef_preserved():
    source = """\
typedef struct {
    int id;
    char name[32];
} User;

User* create_user(int id) {
    User* u = malloc(sizeof(User));
    u->id = id;
    return u;
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "int id;" in result
    assert "char name[32]" in result
    assert "malloc" not in result
    print("TEST typedef_preserved: PASS")


def test_multiline_signature():
    source = """\
int complex_func(int a,
                 int b,
                 int c)
{
    return a + b + c;
}"""
    skel = Skeletonizer()
    result = skel.skeletonize(source)
    assert "{ /* ... */ }" in result
    assert "return a + b + c" not in result
    assert "complex_func" in result
    print("TEST multiline_signature: PASS")


def test_stats():
    source = """\
void a(void) {
    line1;
    line2;
}

void b(void) {
    line3;
}"""
    result, stats = skeletonize_source(source)
    assert stats["functions_stripped"] == 2
    assert stats["lines_removed"] > 0
    assert stats["original_lines"] == 8
    print(f"TEST stats: PASS - {stats}")


if __name__ == "__main__":
    test_simple_function()
    test_struct_preserved()
    test_preprocessor_preserved()
    test_multiple_functions()
    test_nested_braces_in_body()
    test_enum_preserved()
    test_typedef_preserved()
    test_multiline_signature()
    test_stats()
    print("\nAll skeletonizer tests passed!")
