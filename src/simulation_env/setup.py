import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'simulation_env'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'tests']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'maps'),
            [f for f in glob('maps/*') if os.path.isfile(f)]),
        (os.path.join('share', package_name, 'worlds'), glob('worlds/*.world')),
        (os.path.join('share', package_name, 'config'), glob('config/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mohammad Ibrahim Memon',
    maintainer_email='immemon1811@gmail.com',
    description='Grid warehouse fleet execution and Gazebo visualization.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fleet_node = simulation_env.fleet_node:main',
        ],
    },
)
