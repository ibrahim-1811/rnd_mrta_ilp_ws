from setuptools import find_packages, setup

package_name = "mrta_core"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test", "tests"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Mohammad Ibrahim Memon",
    maintainer_email="immemon1811@gmail.com",
    description="Grid-aware MRTA core: A* grid planning, robot/task models, and "
    "symbolic ILP predicate generation.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [],
    },
)
