# Python Snowpark

## To develop Python Snowpark

### Setup Python Environment

#### Install Python

Python Snowpark requires Python 3.8 (or higher). Python 3.8 is available in the default CentOS 7
repository and can be installed from source using the following commands:
```bash
# Install necessary install packages
sudo yum install gcc openssl-devel bzip2-devel libffi-devel sqlite-devel
cd /opt
# Following operations may require sudo
# Go here (https://www.python.org/downloads/) to find the latest Python 3.8 version
wget https://www.python.org/ftp/python/3.8.11/Python-3.8.11.tgz
tar xvf Python-3.8.11.tgz
cd Python-3.8.11/
./configure --enable-optimizations
# Run altinstall to make sure you don't thrash your own system Python install
sudo make altinstall
# Check Python version
python3.8 --version
```
The `devel` package installs the Python headers necessary to compile C-extensions of Snowflake
Python Connector.

#### Clone the repository

```bash
git clone git@github.com:snowflakedb/snowpark-python.git
cd snowpark-python
```

#### Setup a virtualenv

A virtualenv should be installed, which can separate your development with the system-wide Python,
and benefits us an easy package management. Assume you are using Python 3.6, then just run:
```bash
python3.8 -m pip install -U setuptools pip virtualenv
python3.8 -m virtualenv venv
source venv/bin/activate
```
Note that you should activate your virtualenv after rebooting your machine and before
developing Python Snowpark every time.


### Install the Python Snowpark and its dependencies
```bash
python -m pip install ".[development, pandas]"
```


### Setup Pycharm

#### Download and install Pycharm
Download the newest community version of [Pycharm](https://www.jetbrains.com/pycharm/download/)
and follow [installation instructions](https://www.jetbrains.com/help/pycharm/installation-guide.html#snap-install-tar)
for CentOS 7.

#### Setup project
Open project and browse to the cloned git directory. Then right-click the directory `src` in Pycharm
and "Mark Directory as" -> "Source Root".

#### Setup Python Interpreter
This should be setup automatically, but make sure that the Python interpreter in Pycharm is pointing
to the Python binary in your virtualenv (`[cloned repo directory]/virtualenv/bin/python`).
