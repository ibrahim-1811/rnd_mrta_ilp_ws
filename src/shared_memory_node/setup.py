import os

from setuptools import find_packages, setup

package_name = 'shared_memory_node'


def package_files(directory):
    """Recursively collect all files under a directory."""
    paths = []
    for (path, _directories, filenames) in os.walk(directory):
        for filename in filenames:
            paths.append(os.path.join(path, filename))
    return paths


database_files = package_files('databases')

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'tests']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/databases', database_files),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mohammad Ibrahim Memon',
    maintainer_email='immemon1811@gmail.com',
    description='Centralized warehouse database node (shelves, drop-off, inventory).',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'shared_memory_node = shared_memory_node.shared_memory_node:main',
        ],
    },
)
