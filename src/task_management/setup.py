import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'task_management'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'tests']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Mohammad Ibrahim Memon',
    maintainer_email='immemon1811@gmail.com',
    description='MRTA task manager, allocation strategies, loggers, order generator.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'task_manager_node = task_management.task_manager:main',
            'ilp_dataset_logger = task_management.ilp_dataset_logger:main',
            'metrics_logger = task_management.metrics_logger:main',
            'order_generator = task_management.order_generator:main',
        ],
    },
)
