#
# Copyright (c) 2012-2023 Snowflake Computing Inc. All rights reserved.
#
# The code in this file is largely a copy of https://github.com/Snowflake-Labs/snowcli/blob/main/src/snowcli/utils.py

import glob
import os
import platform
import subprocess
import sys
import zipfile
from logging import getLogger
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pkg_resources
from pkg_resources import Requirement

_logger = getLogger(__name__)
PIP_ENVIRONMENT_VARIABLE = "PIP_NAME"
IMPLICIT_ZIP_FILE_NAME = "zipped_packages"
SNOWPARK_PACKAGE_NAME = "snowflake-snowpark-python"


def get_package_name_from_metadata(metadata_file_path: str) -> Optional[str]:
    """
    Loads a METADATA file from the dist-info directory of an installed Python package, finds the name and version of the
    package. The name is found on a line containing "Name: my_package" and version can be found on the line containing
    "Version: version".

    Args:
        metadata_file_path (str): The path to the METADATA file.

    Returns:
        str: The name and (if present) version of the package formatted as f"{package}==[version]".
    """
    import re

    with open(metadata_file_path, encoding="utf-8") as metadata_file:
        contents = metadata_file.read()
        results = re.search("^Name: (.*)$", contents, flags=re.MULTILINE)
        if results is None:
            return None
        requirement_line = results.group(1)
        results = re.search("^Version: (.*)$", contents, flags=re.MULTILINE)
        if results is not None:
            version = results.group(1)
            requirement_line += f"=={version}"
        return requirement_line.strip().lower()


def get_downloaded_packages(directory: str) -> Dict[Requirement, List[str]]:
    """
    Records correspondence between installed python packages and their folder structure, using the RECORD file present
    in most pypi packages. We use the METADATA file to deduce the package name and version, and RECORD file to map
    correspondence between package names and folders/files.

    Args:
        directory (str): Target folder in which pip installed the packages.

    Returns:
        Dict[Requirement, List[str]: Mapping from package to a list of unique folder/file names that correspond to it.
    """
    import glob
    import os

    metadata_files = glob.glob(os.path.join(directory, "*dist-info", "METADATA"))
    package_name_to_record_entries_map: Dict[Requirement, List[str]] = {}
    for metadata_file in metadata_files:
        parent_folder = os.path.dirname(metadata_file)
        package = get_package_name_from_metadata(metadata_file)

        if package is not None:
            # Determine which folders or files belong to this package
            record_file_path = os.path.join(parent_folder, "RECORD")
            if os.path.exists(record_file_path):
                # Get unique root folder names

                with open(record_file_path, encoding="utf-8") as record_file:
                    record_entries = set()
                    for line in record_file.readlines():
                        entry = os.path.split(line)[0].split(",")[0]
                        if entry == "":
                            entry = line.split(",")[0]
                        record_entries.add(entry)

                    included_record_entries = []
                    for record_entry in record_entries:
                        record_entry_full_path = os.path.abspath(
                            os.path.join(directory, record_entry),
                        )
                        # RECORD file might contain relative paths to items outside target folder. (ignore these)
                        if (
                            os.path.exists(record_entry_full_path)
                            and directory in record_entry_full_path
                            and len(record_entry) > 0
                        ):
                            included_record_entries.append(record_entry)
                    package_req = Requirement.parse(package)
                    package_name_to_record_entries_map[
                        package_req
                    ] = included_record_entries
    return package_name_to_record_entries_map


def identify_supported_packages(
    packages: List[Requirement],
    valid_packages: Dict[str, List[str]],
    native_packages: Set[str],
) -> Tuple[List[Requirement], List[Requirement], List[Requirement]]:
    """
    Detects which `packages` are present in the Snowpark Anaconda channel using the `valid_packages` mapping.
    If a package is a native dependency (belongs to `native_packages` set) and supported in Anaconda, we switch to
    the latest available version in Anaconda.

    Note that we also update the native_packages set to reflect genuinely problematic native dependencies, i.e.
    packages that are not present in Anaconda and are likely to cause errors.

    Args:
        packages (List[Requirement]): List of python packages.
        valid_packages (Dict[str, List[str]): Mapping from package name to a list of versions available on the Anaconda
        channel.
        native_packages (Set[str]): Set of native dependency package names.

    Returns:
        Tuple[List[Requirement], List[Requirement], List[Requirement]]: Tuple containing dependencies that are present
        in Anaconda, dependencies that should be dropped from the package list and dependencies that should be added.
    """
    supported_dependencies: List = []
    dropped_dependencies: List = []
    new_dependencies: List = []
    for package in packages:
        package_name = package.name
        package_version_req = package.specs[0][1] if package.specs else None
        if package_name in valid_packages:
            if (package_version_req is None) or (
                package_version_req in valid_packages[package_name]
            ):
                supported_dependencies.append(package)
            elif package_name in native_packages:
                # Native packages should anaconda dependencies if possible, even if the version is not available
                _logger.warning(
                    f"Package {package_name}(version {package_version_req}) is an unavailable native "
                    f"dependency, switching to latest available version "
                    f"{valid_packages[package_name][-1]} instead."
                )
                dropped_dependencies.append(package)
                new_dependencies.append(Requirement.parse(package_name))

            if package_name in native_packages:
                native_packages.remove(package_name)
    return supported_dependencies, dropped_dependencies, new_dependencies


def pip_install_packages_to_target_folder(packages: List[str], target: str) -> None:
    """
    Pip installs specified `packages at folder specified as `target`.

    Args:
        packages (List[str]): List of pypi packages.
        target (str): Target directory (absolute path).

    Raises:
        ModuleNotFoundError: If pip is not present.
        RuntimeError: If pip fails to install the packages.
    """
    try:
        pip_executable = os.getenv(PIP_ENVIRONMENT_VARIABLE)
        pip_command = (
            [sys.executable, "-m", "pip"] if not pip_executable else [pip_executable]
        )
        process = subprocess.Popen(
            pip_command + ["install", "-t", target, *packages],
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        process.wait()
        pip_install_result = process.returncode
        process_output = "\n".join([line.strip() for line in process.stdout])
        _logger.debug(process_output)
    except FileNotFoundError:
        raise ModuleNotFoundError(
            f"Pip not found. Please install pip in your environment or specify the path to your pip executable as "
            f"'{PIP_ENVIRONMENT_VARIABLE}' environment variable and try again."
        )

    if pip_install_result is not None and pip_install_result != 0:
        raise RuntimeError(f"Pip failed with return code {pip_install_result}.")


def detect_native_dependencies(
    target: str, downloaded_packages_dict: Dict[Requirement, List[str]]
) -> Set[str]:
    """
    Detects files with native extensions present at the `target` folder, and deduces which packages own these files.
    Native dependencies use C/C++ code that won't work when uploaded via a zip file. We detect these so that we can
    switch to Anaconda-supported versions of these packages, where possible (or warn the user if it is not possible).

    We detect native dependency by looking for file extensions that correspond to native code usage (Note that this
    method is best-effort and will result in both false positives and negatives).

    Args:
        target (str): Target directory which contains packages installed by pip.
        downloaded_packages_dict (Dict[Requirement, List[str]]): Mapping between packages and a list of files or
        folders corresponding to it.

    Returns:
        Set[str]: Set of native dependency names. Note that we only return a set of strings here rather than Requirement
        objects because the specific version of a native package is irrelevant.
    """

    def invert_downloaded_package_to_entry_map(
        downloaded_packages_dict: Dict[Requirement, List[str]]
    ) -> Dict[str, Set[str]]:
        """
        Invert dictionary mapping packages to files/folders. We need this dictionary to be inverted because we first
        discover files with native dependency extensions and then need to deduce the packages corresponding to these
        files.

        Args:
            downloaded_packages_dict (Dict[Requirement, List[str]]): Mapping between packages and a list of files or
            folders corresponding to it.

        Returns:
            Dict[str, Set[str]]: The inverse mapping from a file or folder to the packages it corresponds to. Note that
            it is unlikely a file corresponds to multiple packages (but we allow for the possibility). We only need
            to return a set of strings here rather than Requirement objects because the specific version of a native
            package is irrelevant.
        """
        record_entry_to_package_name_map: Dict[str, Set[str]] = {}
        for requirement, record_entries in downloaded_packages_dict.items():
            for record_entry in record_entries:
                if record_entry not in record_entry_to_package_name_map:
                    record_entry_to_package_name_map[record_entry] = {requirement.name}
                else:
                    record_entry_to_package_name_map[record_entry].add(requirement.name)
        return record_entry_to_package_name_map

    native_libraries = set()
    native_extensions = {
        ".pyd",
        ".pyx",
        ".pxd",
        ".dll" if platform.system() == "Windows" else ".so",
    }
    for native_extension in native_extensions:
        glob_output = glob.glob(
            os.path.join(target, "**", f"*{native_extension}")
        ) + glob.glob(os.path.join(target, f"*{native_extension}"))
        if glob_output:
            folder_to_package_map = invert_downloaded_package_to_entry_map(
                downloaded_packages_dict
            )
            for path in glob_output:
                relative_path = os.path.relpath(path, target)

                # Fetch record entry (either base directory or a file name if base directory is target)
                record_entry = os.path.split(relative_path)[0]
                if record_entry == "":
                    record_entry = relative_path

                # Check which package owns this record entry
                if record_entry in folder_to_package_map:
                    library_set = folder_to_package_map[record_entry]
                    for library in library_set:
                        if library not in native_libraries:
                            _logger.info(f"Potential native library: {library}")
                            native_libraries.add(library)
    return native_libraries


def zip_directory_contents(target: str, output_path: str) -> None:
    """
    Zips all files/folders inside the directory path as well as those installed one level up from the directory path.

    Args:
        target (str): Target directory (absolute path) which contains packages installed by pip.
        output_path (str): Absolute path for output zip file.
    """
    target = Path(target)
    output_path = Path(output_path)
    with zipfile.ZipFile(
        output_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zipf:
        for file in target.rglob("*"):
            zipf.write(file, file.relative_to(target))

        parent_directory = target.parent

        for file in parent_directory.iterdir():
            if (
                file.is_file()
                and not file.match(".*")
                and file != output_path
                and file != target
            ):
                zipf.write(file, file.relative_to(parent_directory))


def add_snowpark_package(
    package_dict: Dict[str, str], valid_packages: Dict[str, List[str]]
) -> None:
    """
    Adds the Snowpark Python package to package dictionary, if not present. We either choose the version available in
    the local environemnt or latest available on Anaconda.

    Args:
        package_dict (Dict[str, str]): Package dictionary passed in from Session object.
        valid_packages (Dict[str, List[str]]): Mapping from package name to a list of versions available on the Anaconda
        channel.

    Raises:
        pkg_resources.DistributionNotFound: If the Snowpark Python Package is not installed in the local environment.
    """
    if SNOWPARK_PACKAGE_NAME not in package_dict:
        package_dict[SNOWPARK_PACKAGE_NAME] = SNOWPARK_PACKAGE_NAME
        try:
            package_client_version = pkg_resources.get_distribution(
                SNOWPARK_PACKAGE_NAME
            ).version
            if package_client_version in valid_packages[SNOWPARK_PACKAGE_NAME]:
                package_dict[
                    SNOWPARK_PACKAGE_NAME
                ] = f"{SNOWPARK_PACKAGE_NAME}=={package_client_version}"
            else:
                _logger.warning(
                    f"The version of package '{SNOWPARK_PACKAGE_NAME}=={package_client_version}' in the local environment is "
                    f"{package_client_version}, which is not available in Snowflake. Your UDF might not work when "
                    f"the package version is different between the server and your local environment."
                )
        except pkg_resources.DistributionNotFound:
            _logger.warning(
                f"Package '{SNOWPARK_PACKAGE_NAME}' is not installed in the local environment. "
                f"Your UDF might not work when the package is installed on the server "
                f"but not on your local environment."
            )
        except Exception as ex:  # pragma: no cover
            _logger.warning(
                "Failed to get the local distribution of package %s: %s",
                SNOWPARK_PACKAGE_NAME,
                ex,
            )
