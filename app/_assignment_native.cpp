// Native maximum-weight bipartite matching core.
//
// Same formulation as the Python implementation: rectangular optional
// matching reduced to a square potential-method assignment where every row
// has a private zero-cost dummy column.  The exposed primitive takes an n x m
// cost matrix (n <= m) containing INT64_MIN for incompatible cells and
// returns the row matched to each column (-1 when free).
//
// Deterministic tie-breaking: columns are scanned in ascending order with
// strict comparisons.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <vector>

namespace {

constexpr int64_t kIncompatible = std::numeric_limits<int64_t>::MAX / 4;

// Hungarian / potential method, minimization.
// a is n rows x m columns (n <= m), flattened row-major. Ownership of values
// is read-only here. Potentials and slacks use 128-bit integers so repeated
// additions of the incompatibility sentinel can never overflow.
std::vector<int> hungarian(int n, int m, const int64_t* a) {
    using W = __int128_t;
    const W inf = static_cast<W>(kIncompatible);
    std::vector<W> u(n + 1, 0), v(m + 1, 0);
    std::vector<int> p(m + 1, 0), way(m + 1, 0);

    for (int i = 1; i <= n; ++i) {
        p[0] = i;
        int j0 = 0;
        std::vector<W> minv(m + 1, inf);
        std::vector<char> used(m + 1, 0);
        do {
            used[j0] = 1;
            int i0 = p[j0];
            const int64_t* row = a + static_cast<int64_t>(i0 - 1) * m;
            W delta = inf;
            int j1 = 0;
            W ui0 = u[i0];
            for (int j = 1; j <= m; ++j) {
                if (!used[j]) {
                    W cur = static_cast<W>(row[j - 1]) - ui0 - v[j];
                    if (cur < minv[j]) {
                        minv[j] = cur;
                        way[j] = j0;
                    }
                    if (minv[j] < delta) {
                        delta = minv[j];
                        j1 = j;
                    }
                }
            }
            for (int j = 0; j <= m; ++j) {
                if (used[j]) {
                    u[p[j]] += delta;
                    v[j] -= delta;
                } else {
                    minv[j] -= delta;
                }
            }
            j0 = j1;
        } while (p[j0] != 0);

        do {
            int j1 = way[j0];
            p[j0] = p[j1];
            j0 = j1;
        } while (j0 != 0);
    }

    std::vector<int> result(m, -1);
    for (int j = 1; j <= m; ++j) {
        int r = p[j];
        if (r != 0 && a[static_cast<int64_t>(r - 1) * m + (j - 1)] <
                          kIncompatible / 2) {
            result[j - 1] = r - 1;
        }
    }
    return result;
}

PyObject* py_hungarian(PyObject* /*self*/, PyObject* args) {
    PyObject* list_obj;
    if (!PyArg_ParseTuple(args, "O", &list_obj)) {
        return nullptr;
    }
    PyObject* seq_fast = PySequence_Fast(list_obj, "expected a list");
    if (!seq_fast) return nullptr;
    Py_ssize_t n = PySequence_Fast_GET_SIZE(seq_fast);
    if (n <= 0) {
        Py_DECREF(seq_fast);
        return PyList_New(0);
    }
    PyObject* first = PySequence_Fast_GET_ITEM(seq_fast, 0);
    PyObject* row_fast = PySequence_Fast(first, "rows must be lists");
    if (!row_fast) {
        Py_DECREF(seq_fast);
        return nullptr;
    }
    Py_ssize_t m = PySequence_Fast_GET_SIZE(row_fast);
    Py_DECREF(row_fast);
    if (m <= 0 || n > m) {
        Py_DECREF(seq_fast);
        PyErr_SetString(PyExc_ValueError, "require 0 < n <= m");
        return nullptr;
    }

    std::vector<int64_t> matrix(static_cast<size_t>(n) * m);
    for (Py_ssize_t i = 0; i < n; ++i) {
        PyObject* row = PySequence_Fast(PySequence_Fast_GET_ITEM(seq_fast, i),
                                        "rows must be lists");
        if (!row) {
            Py_DECREF(seq_fast);
            return nullptr;
        }
        if (PySequence_Fast_GET_SIZE(row) != m) {
            Py_DECREF(row);
            Py_DECREF(seq_fast);
            PyErr_SetString(PyExc_ValueError, "ragged matrix");
            return nullptr;
        }
        int64_t* dst = &matrix[static_cast<size_t>(i) * m];
        for (Py_ssize_t j = 0; j < m; ++j) {
            PyObject* cell = PySequence_Fast_GET_ITEM(row, j);
            if (cell == Py_None) {
                dst[j] = kIncompatible;
            } else {
                int overflow = 0;
                long long value = PyLong_AsLongLongAndOverflow(cell, &overflow);
                if (overflow != 0 || PyErr_Occurred()) {
                    Py_DECREF(row);
                    Py_DECREF(seq_fast);
                    if (!PyErr_Occurred()) {
                        PyErr_SetString(PyExc_OverflowError,
                                        "cost out of int64 range");
                    }
                    return nullptr;
                }
                dst[j] = value;
            }
        }
        Py_DECREF(row);
    }
    Py_DECREF(seq_fast);

    std::vector<int> result =
        hungarian(static_cast<int>(n), static_cast<int>(m), matrix.data());

    PyObject* out = PyList_New(m);
    if (!out) return nullptr;
    for (Py_ssize_t j = 0; j < m; ++j) {
        PyObject* val = PyLong_FromLong(result[j]);
        if (!val) {
            Py_DECREF(out);
            return nullptr;
        }
        PyList_SET_ITEM(out, j, val);
    }
    return out;
}

PyMethodDef methods[] = {
    {"hungarian", py_hungarian, METH_VARARGS,
     "hungarian(matrix) -> column matches (row or -1)"},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_assignment_native", nullptr, -1, methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit__assignment_native(void) {
    return PyModule_Create(&moduledef);
}
