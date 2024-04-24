#
# Copyright (c) 2012-2024 Snowflake Computing Inc. All rights reserved.
#

# Licensed to Modin Development Team under one or more contributor license agreements.
# See the NOTICE file distributed with this work for additional information regarding
# copyright ownership.  The Modin Development Team licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except in
# compliance with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific language
# governing permissions and limitations under the License.

# Code in this file may constitute partial or total reimplementation, or modification of
# existing code originally distributed by the Modin project, under the Apache License,
# Version 2.0.

"""Module houses default binary functions builder class."""
from typing import Any, Callable, Union

import pandas
from pandas._typing import AnyArrayLike, Scalar

from snowflake.snowpark.modin.core.dataframe.algebra.default2pandas.default import (
    DefaultMethod,
)


class BinaryDefault(DefaultMethod):
    """Build default-to-pandas methods which executes binary functions."""

    @classmethod
    def build_default_to_pandas(cls, fn: Callable, fn_name: str) -> Callable:
        """
        Build function that do fallback to pandas for passed binary `fn`.

        Parameters
        ----------
        fn : callable
            Binary function to apply to the casted to pandas frame and other operand.
        fn_name : str
            Function name which will be shown in default-to-pandas warning message.

        Returns
        -------
        callable
            Function that takes query compiler, does fallback to pandas and applies binary `fn`
            to the casted to pandas frame.
        """

        def bin_ops_wrapper(
            df: pandas.DataFrame,
            other: Union[pandas.DataFrame, pandas.Series, Scalar, AnyArrayLike],
            *args: Any,
            **kwargs: Any
        ) -> pandas.DataFrame:
            """Apply specified binary function to the passed operands."""
            squeeze_other = kwargs.pop("broadcast", False) or kwargs.pop(
                "squeeze_other", False
            )
            squeeze_self = kwargs.pop("squeeze_self", False)

            if squeeze_other:
                other = other.squeeze(axis=1)

            if squeeze_self:
                df = df.squeeze(axis=1)

            result = fn(df, other, *args, **kwargs)
            if not isinstance(result, pandas.DataFrame):  # pragma: no cover
                result = pandas.DataFrame(result)
            return result

        return super().build_default_to_pandas(bin_ops_wrapper, fn_name)
