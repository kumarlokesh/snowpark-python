#
# Copyright (c) 2012-2023 Snowflake Computing Inc. All rights reserved.
#

import datetime
import logging
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from snowflake.snowpark import Row
from snowflake.snowpark._internal.packaging_utils import (
    ENVIRONMENT_METADATA_FILE_NAME,
    IMPLICIT_ZIP_FILE_NAME,
)
from snowflake.snowpark.functions import col, count_distinct, sproc, udf
from snowflake.snowpark.types import DateType, StringType
from tests.utils import IS_IN_STORED_PROC, IS_WINDOWS, TempObjectType, TestFiles, Utils

try:
    import dateutil

    # six is the dependency of dateutil
    import six
    from dateutil.relativedelta import relativedelta

    is_dateutil_available = True
except ImportError:
    is_dateutil_available = False

try:
    import numpy
    import pandas

    is_pandas_and_numpy_available = True
except ImportError:
    is_pandas_and_numpy_available = False


@pytest.fixture(scope="module", autouse=True)
def setup(session, resources_path):
    tmp_stage_name = Utils.random_stage_name()
    test_files = TestFiles(resources_path)
    Utils.create_stage(session, tmp_stage_name, is_temporary=True)
    Utils.upload_to_stage(
        session, tmp_stage_name, test_files.test_udf_py_file, compress=False
    )


@pytest.fixture(autouse=True)
def clean_up(session):
    session._session_stage = Utils.random_stage_name()
    session._stage_created = False
    session.clear_packages()
    session.clear_imports()
    session._runtime_version_from_requirement = None
    yield


@pytest.fixture(scope="function")
def temporary_stage(session):
    temporary_stage_name = Utils.random_stage_name()
    Utils.create_stage(session, temporary_stage_name, is_temporary=True)
    yield temporary_stage_name


@pytest.fixture(scope="function")
def bad_yaml_file():
    # Generate a bad YAML string
    bad_yaml = """
    some_key: some_value:
        - list_item1
        - list_item2
    """

    # Write the bad YAML to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as file:
        file.write(bad_yaml)
        file_path = file.name

    yield file_path

    # Clean up the temporary file after the test completes
    if file_path:
        os.remove(file_path)


@pytest.fixture(scope="function")
def ranged_yaml_file():
    # Generate a bad YAML string
    bad_yaml = """
    name: my_environment  # Name of the environment
    channels:  # List of Conda channels to use for package installation
      - conda-forge
      - defaults
    dependencies:  # List of packages and versions to include in the environment
      - python=3.9  # Python version
      - numpy<=1.24.3
    """

    # Write the ranged YAML to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".yaml") as file:
        file.write(bad_yaml)
        file_path = file.name

    yield file_path

    # Clean up the temporary file after the test completes
    if os.path.exists(file_path):
        os.remove(file_path)


@pytest.mark.skipif(
    (not is_pandas_and_numpy_available) or IS_IN_STORED_PROC,
    reason="numpy and pandas are required",
)
def test_add_packages(session):
    session.add_packages(
        [
            "numpy==1.23.5",
            "pandas==1.5.3",
            "matplotlib",
            "pyyaml",
        ]
    )
    assert session.get_packages() == {
        "numpy": "numpy==1.23.5",
        "pandas": "pandas==1.5.3",
        "matplotlib": "matplotlib",
        "pyyaml": "pyyaml",
    }

    # dateutil is a dependency of pandas
    def get_numpy_pandas_dateutil_version() -> str:
        return f"{numpy.__version__}/{pandas.__version__}/{dateutil.__version__}"

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)
    session.udf.register(get_numpy_pandas_dateutil_version, name=udf_name)
    # don't need to check the version of dateutil, as it can be changed on the server side
    assert (
        session.sql(f"select {udf_name}()").collect()[0][0].startswith("1.23.5/1.5.3")
    )

    # only add pyyaml, which will overwrite the previously added packages
    # so matplotlib will not be available on the server side
    def is_matplotlib_available() -> bool:
        try:
            import matplotlib.pyplot as plt  # noqa: F401
        except ModuleNotFoundError:
            return False
        return True

    session.udf.register(
        is_matplotlib_available, name=udf_name, replace=True, packages=["pyyaml"]
    )
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row(False)])

    # with an empty list of udf-level packages
    # it will still fail even if we have session-level packages
    def is_yaml_available() -> bool:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            return False
        return True

    session.udf.register(is_yaml_available, name=udf_name, replace=True, packages=[])
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row(False)])

    session.clear_packages()

    session.udf.register(
        is_yaml_available, name=udf_name, replace=True, packages=["pyyaml"]
    )
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row(True)])

    session.clear_packages()

    # add module objects
    # but we can't register a udf with these versions
    # because the server might not have them
    resolved_packages = session._resolve_packages(
        [numpy, pandas, dateutil], validate_package=False
    )
    assert f"numpy=={numpy.__version__}" in resolved_packages
    assert f"pandas=={pandas.__version__}" in resolved_packages
    assert f"python-dateutil=={dateutil.__version__}" in resolved_packages

    session.clear_packages()


def test_add_packages_with_underscore(session):
    packages = ["spacy-model-en_core_web_sm", "typing_extensions"]
    count = (
        session.table("information_schema.packages")
        .where(col("package_name").in_(packages))
        .select(count_distinct("package_name"))
        .collect()[0][0]
    )
    if count != len(packages):
        pytest.skip("These packages with underscores are not available")

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name, packages=packages)
    def check_if_package_installed() -> bool:
        try:
            import spacy
            import typing_extensions  # noqa: F401

            spacy.load("en_core_web_sm")
            return True
        except Exception:
            return False

    Utils.check_answer(session.sql(f"select {udf_name}()").collect(), [Row(True)])


@pytest.mark.skipif(
    IS_IN_STORED_PROC, reason="Need certain version of datautil/pandas/numpy"
)
def test_add_packages_negative(session, caplog):
    with pytest.raises(ValueError) as ex_info:
        session.add_packages("python-dateutil****")
    assert "InvalidRequirement" in str(ex_info)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        with pytest.raises(RuntimeError) as ex_info:
            session.add_packages("dateutil")

        # dateutil is not a valid name, the library name is python-dateutil
        assert "Pip failed with return code 1" in str(ex_info)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: False):
        with pytest.raises(RuntimeError) as ex_info:
            session.add_packages("dateutil")

        assert "Cannot add package dateutil" in str(ex_info)

    with pytest.raises(ValueError) as ex_info:
        with caplog.at_level(logging.WARNING):
            # using numpy version 1.16.6 here because using any other version raises a
            # ValueError for "non-existent python version in Snowflake" instead of
            # "package is already added".
            # In case this test fails in the future, choose a version of numpy which
            # is supportezd by Snowflake using query:
            #     select package_name, array_agg(version)
            #     from information_schema.packages
            #     where language='python' and package_name like 'numpy'
            #     group by package_name;
            session.add_packages("numpy", "numpy==1.16.6")
    assert "is already added" in str(ex_info)

    with pytest.raises(ValueError) as ex_info:
        session.remove_package("python-dateutil")
    assert "is not in the package list" in str(ex_info)


@pytest.mark.skipif(
    (not is_pandas_and_numpy_available) or IS_IN_STORED_PROC,
    reason="numpy and pandas are required",
)
def test_add_requirements(session, resources_path):
    test_files = TestFiles(resources_path)

    session.add_requirements(test_files.test_requirements_file)
    assert session.get_packages() == {
        "numpy": "numpy==1.23.5",
        "pandas": "pandas==1.5.3",
    }

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def get_numpy_pandas_version() -> str:
        return f"{numpy.__version__}/{pandas.__version__}"

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("1.23.5/1.5.3")])


def test_add_requirements_twice_should_fail_if_packages_are_different(
    session, resources_path
):
    test_files = TestFiles(resources_path)

    session.add_requirements(test_files.test_requirements_file)
    assert session.get_packages() == {
        "numpy": "numpy==1.23.5",
        "pandas": "pandas==1.5.3",
    }

    with pytest.raises(ValueError) as ex_info:
        session.add_packages(["numpy==1.23.4"])
    assert "Cannot add package" in str(ex_info)


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_unsupported_requirements_twice_should_not_fail_for_same_requirements_file(
    session, resources_path
):
    test_files = TestFiles(resources_path)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_requirements(test_files.test_unsupported_requirements_file)
        assert set(session.get_packages().keys()) == {
            "scipy",
            "numpy",
            "matplotlib",
            "pyyaml",
        }

        session.add_requirements(test_files.test_unsupported_requirements_file)
        assert set(session.get_packages().keys()) == {
            "scipy",
            "numpy",
            "matplotlib",
            "pyyaml",
        }


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_packages_should_fail_if_dependency_package_already_added(session):
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_packages(["scikit-learn==1.2.0"])
        with pytest.raises(ValueError) as ex_info:
            session.add_packages("sktime")
        assert "Cannot add dependency package" in str(ex_info)


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_requirements_unsupported(session, resources_path):
    test_files = TestFiles(resources_path)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_requirements(test_files.test_unsupported_requirements_file)
        # Once scikit-fuzzy is supported, this test will break; change the test to a different unsupported module
        assert set(session.get_packages().keys()) == {
            "matplotlib",
            "pyyaml",
            "scipy",
            "numpy",
        }

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def import_scikit_fuzzy() -> str:
        import skfuzzy as fuzz

        return fuzz.__version__

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.4.2")])


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_requirements_with_native_dependency_force_push(session):
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_packages(["catboost"], force_push=True)
    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def check_if_package_works() -> str:
        try:
            import catboost

            return str(catboost)
        except Exception:
            return "does not work"

    # Unsupported native dependency, the code doesn't run
    Utils.check_answer(
        session.sql(f"select {udf_name}()").collect(),
        [Row("does not work")],
    )


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_requirements_with_native_dependency_without_force_push(session):
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        with pytest.raises(RuntimeError) as ex_info:
            session.add_packages(["catboost"])
        assert "Your code depends on native dependencies" in str(ex_info)


@pytest.fixture(scope="function")
def requirements_file_with_local_path():
    # Write a local script to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".py`") as file:
        file.write("VARIABLE_IN_LOCAL_FILE = 50")
        local_script_path = file.name

    local_script_basedir = os.path.dirname(local_script_path)
    new_path = os.path.join(local_script_basedir, "nicename.py")
    os.rename(local_script_path, new_path)

    # Generate a requirements file
    requirements = f"""
    pyyaml==6.0
    matplotlib
    {new_path}
    """
    # Write the bad YAML to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as file:
        file.write(requirements)
        requirements_path = file.name

    yield requirements_path

    # Clean up the temporary files after the test completes
    for path in {requirements_path, local_script_path, new_path}:
        if os.path.exists(path):
            os.remove(path)


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="matplotlib required",
)
def test_add_requirements_with_local_filepath(
    session, requirements_file_with_local_path
):
    """
    Assert that is a requirement file references local python scripts, the variables in those local python scripts
    are available for use within a UDF.
    """
    session.add_requirements(requirements_file_with_local_path)
    assert session.get_packages() == {
        "matplotlib": "matplotlib",
        "pyyaml": "pyyaml==6.0",
    }

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def use_local_file_variables() -> str:
        from nicename import VARIABLE_IN_LOCAL_FILE

        return f"{VARIABLE_IN_LOCAL_FILE + 10}"

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("60")])


def test_add_requirements_yaml(session, resources_path):
    test_files = TestFiles(resources_path)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_requirements(test_files.test_conda_environment_file)
    assert session.get_packages().keys() == {
        "numpy",
        "pandas",
        "scikit-learn",
        "matplotlib",
        "seaborn",
        "scipy",
    }
    assert session._runtime_version_from_requirement == "3.8"

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)
    system_version = f"{sys.version_info[0]}.{sys.version_info[1]}"

    if system_version != session._runtime_version_from_requirement:
        with pytest.raises(ValueError) as ex_info:

            @udf(name=udf_name)
            def get_numpy_pandas_version() -> str:
                import scipy
                import seaborn as sns
                import tensorflow as tf

                return f"{tf.__version__}/{sns.__version__}/{scipy.__version__}"

        assert (
            "Cloudpickle can only be used to send objects between the exact same version of Python. "
            in str(ex_info)
        )
    else:

        @udf(name=udf_name)
        def get_numpy_pandas_version() -> str:
            import scipy
            import seaborn as sns

            return f"{sns.__version__}/{scipy.__version__}"

        Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.11.1/1.10.1")])


def test_add_requirements_with_bad_yaml(session, bad_yaml_file):
    with pytest.raises(ValueError) as ex_info:
        session.add_requirements(bad_yaml_file)
    assert (
        "Error while parsing YAML file, it may not be a valid Conda environment file"
        in str(ex_info)
    )


def test_add_requirements_with_ranged_requirements_in_yaml(session, ranged_yaml_file):
    with pytest.raises(ValueError) as ex_info:
        session.add_requirements(ranged_yaml_file)
    assert "Conda dependency with ranges 'numpy<=1.24.3' is not supported" in str(
        ex_info
    )


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_packages_unsupported_during_udf_registration(session):
    """
    Assert that unsupported packages can directly be added while registering UDFs.
    """
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        packages = ["scikit-fuzzy==0.4.2"]
        udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

        @udf(name=udf_name, packages=packages)
        def check_if_package_works() -> str:
            try:
                import skfuzzy as fuzz

                return fuzz.__version__
            except Exception as e:
                return f"Import statement does not work: {e.__repr__()}"

        Utils.check_answer(
            session.sql(f"select {udf_name}()").collect(),
            [Row("0.4.2")],
        )


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_packages_unsupported_during_sproc_registration(session):
    """
    Assert that unsupported packages can directly be added while registering Stored Procedures.
    """
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        packages = ["scikit-fuzzy==0.4.2", "snowflake-snowpark-python"]
        sproc_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

        @sproc(name=sproc_name, packages=packages, return_type=StringType())
        def check_if_package_works(session_):
            try:
                import skfuzzy as fuzz

                return fuzz.__version__
            except Exception as e:
                return f"Import statement does not work: {e.__repr__()}"

        assert check_if_package_works() == "0.4.2"


@pytest.mark.skipif(not is_dateutil_available, reason="dateutil is required")
def test_add_import_package(session):
    def plus_one_month(x):
        return x + relativedelta(month=1)

    d = datetime.date.today()
    session.add_import(os.path.dirname(dateutil.__file__))
    session.add_import(six.__file__)
    df = session.create_dataframe([d]).to_df("a")
    plus_one_month_udf = udf(
        plus_one_month, return_type=DateType(), input_types=[DateType()]
    )
    Utils.check_answer(
        df.select(plus_one_month_udf("a")).collect(), [Row(plus_one_month(d))]
    )


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="numpy and pandas are required",
)
def test_add_requirements_with_empty_stage_as_persist_path(
    session, resources_path, temporary_stage
):
    """
    Assert that adding a persist_path (empty stage) does not affect the requirements addition process.
    """
    test_files = TestFiles(resources_path)

    session.add_requirements(
        test_files.test_requirements_file, persist_path=temporary_stage
    )
    assert session.get_packages() == {
        "numpy": "numpy==1.23.5",
        "pandas": "pandas==1.5.3",
    }

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name, packages=["snowflake-snowpark-python==1.3.0"])
    def get_numpy_pandas_version() -> str:
        import snowflake.snowpark as snowpark

        return f"{snowpark.__version__}"

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("1.3.0")])


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="subprocess calls are not possible within a stored procedure",
)
def test_add_requirements_unsupported_with_empty_stage_as_persist_path(
    session, resources_path, temporary_stage
):
    """
    Assert that adding a persist_path (empty stage) does not affect the requirements addition process, even if
    requirements are unsupported.
    """
    test_files = TestFiles(resources_path)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_requirements(
            test_files.test_unsupported_requirements_file, persist_path=temporary_stage
        )

    assert session.get_packages().keys() == {"matplotlib", "numpy", "pyyaml", "scipy"}

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def get_numpy_pandas_version() -> str:
        import skfuzzy as fuzz

        return fuzz.__version__

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.4.2")])


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="subprocess calls are not possible within a stored procedure",
)
def test_add_requirements_unsupported_with_persist_path_negative(
    session, resources_path, temporary_stage
):
    """
    Assert that failure in loading environment from `persist_path` does not affect the requirements addition process,
    even if requirements are unsupported.
    """
    test_files = TestFiles(resources_path)

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        with patch(
            "snowflake.snowpark.session.Session._load_unsupported_packages_from_stage",
            side_effect=Exception("This function does not work"),
        ):
            session.add_requirements(
                test_files.test_unsupported_requirements_file,
                persist_path=temporary_stage,
            )

    assert session.get_packages().keys() == {"matplotlib", "numpy", "pyyaml", "scipy"}

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)

    @udf(name=udf_name)
    def get_numpy_pandas_version() -> str:
        import skfuzzy as fuzz

        return fuzz.__version__

    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.4.2")])


@pytest.mark.skipif(
    IS_IN_STORED_PROC,
    reason="Subprocess calls are not allowed within stored procedures",
)
def test_add_requirements_unsupported_with_persist_path(
    session, resources_path, temporary_stage
):
    """
    Assert that if a persist_path is mentioned, the zipped packages file and a metadata file are present at this
    remote stage path. Also, subsequent attempts to add the same requirements file should result in the zip file
    being directly imported from persist_path (i.e. no pip install, no native package dependency detection, etc).
    We test this by patching the `_upload_unsupported_packages` function to throw an Exception.

    Finally assert that adding a new unsupported packages results in a new environment signature and zip file
    """
    test_files = TestFiles(resources_path)

    # Prove that patching _upload_unsupported_packages leads to failure
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        with patch(
            "snowflake.snowpark.session.Session._upload_unsupported_packages",
            side_effect=Exception("This function should not have been called"),
        ):
            with pytest.raises(Exception) as ex_info:
                session.add_requirements(
                    test_files.test_unsupported_requirements_file,
                    persist_path=temporary_stage,
                )
            assert "This function should not have been called" in str(ex_info)

    session.clear_imports()
    session.clear_packages()

    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_requirements(
            test_files.test_unsupported_requirements_file, persist_path=temporary_stage
        )
        # Once scikit-fuzzy is supported, this test will break; change the test to a different unsupported module

    environment_hash = "43c5b9d5af61620d2efe4e6fafce11901830f080"
    zip_file = f"{IMPLICIT_ZIP_FILE_NAME}_{environment_hash}.zip"
    metadata_file = f"{ENVIRONMENT_METADATA_FILE_NAME}.txt"
    stage_files = session._list_files_in_stage(temporary_stage)

    assert f"{zip_file}.gz" in stage_files
    assert metadata_file in stage_files

    session_imports = session.get_imports()
    assert len(session_imports) == 1
    assert f"{temporary_stage}/{zip_file}" in session_imports[0]
    assert set(session.get_packages().keys()) == {
        "matplotlib",
        "pyyaml",
        "scipy",
        "numpy",
    }

    def import_scikit_fuzzy() -> str:
        import skfuzzy as fuzz

        return f"{fuzz.__version__}"

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)
    session.udf.register(import_scikit_fuzzy, name=udf_name)
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.4.2")])

    session.clear_packages()
    session.clear_imports()

    # Use existing zip file to run the same function again
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        with patch(
            "snowflake.snowpark.session.Session._upload_unsupported_packages",
            side_effect=Exception("This function should not have been called"),
        ):
            # This should not raise error because we no long call _upload_unsupported_packages (we load it from env)
            session.add_requirements(
                test_files.test_unsupported_requirements_file,
                persist_path=temporary_stage,
            )

    stage_files = session._list_files_in_stage(temporary_stage)
    assert f"{zip_file}.gz" in stage_files
    assert metadata_file in stage_files

    session_imports = session.get_imports()
    assert len(session_imports) == 1
    assert f"{temporary_stage}/{zip_file}" in session_imports[0]
    assert set(session.get_packages().keys()) == {
        "matplotlib",
        "pyyaml",
        "scipy",
        "numpy",
    }

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)
    session.udf.register(import_scikit_fuzzy, name=udf_name)
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("0.4.2")])

    # Add a second environment
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.add_packages(["sktime"], persist_path=temporary_stage)

    assert set(session.get_packages().keys()) == {
        "matplotlib",
        "pyyaml",
        "scipy",
        "numpy",
        "pandas",
        "python-dateutil",
        "scikit-learn",
        "six",
        "wrapt",
    }

    # Assert that metadata contains two environment signatures
    metadata_path = f"{temporary_stage}/{metadata_file}"
    metadata = {
        row[0]: row[1].split("|") if row[1] else []
        for row in (
            session.sql(
                f"SELECT t.$1 as signature, t.$2 as packages from '@{metadata_path}' t"
            )._internal_collect_with_tag()
        )
    }
    assert len(metadata) == 2


# TODO: V2 - Fix this test
@pytest.mark.xfail(reason="Flaky test")
@pytest.mark.skipif(
    IS_IN_STORED_PROC or IS_WINDOWS or not is_pandas_and_numpy_available,
    reason="Numpy and pandas needed and subprocess process calls might occur (not allowed inside stored proc). "
    "Also, replicate_local_environment() currently causes an infinite loop in Windows environments.",
)
def test_replicate_local_environment(session):
    with patch.object(session, "_is_anaconda_terms_acknowledged", lambda: True):
        session.replicate_local_environment(
            force_push=True,
            ignore_packages={
                "snowflake-snowpark-python",
            },
        )
    packages = session.get_packages()
    assert len(packages) > 0

    def sample_udf() -> str:
        return "works"

    udf_name = Utils.random_name_for_temp_object(TempObjectType.FUNCTION)
    session.udf.register(sample_udf, name=udf_name)
    Utils.check_answer(session.sql(f"select {udf_name}()"), [Row("works")])
