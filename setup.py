from setuptools import Extension, setup

setup(
    name="assignment-native",
    ext_modules=[
        Extension(
            "app._assignment_native",
            sources=["app/_assignment_native.cpp"],
            language="c++",
            extra_compile_args=["-O2", "-std=c++17"],
        )
    ],
)
