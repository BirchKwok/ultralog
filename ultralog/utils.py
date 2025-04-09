import os
import logging
from typing import Any, TypeVar, Callable, Optional, Union, Type

T = TypeVar('T')


def get_env_variable(
    name: str, 
    default: Any = None, 
    default_type: Optional[Union[Type[T], Callable[[str], T]]] = None
) -> Any:
    """Get environment variable value, support type conversion and error handling
    
    Parameters
    ----------
    name : str
        Environment variable name
    default : Any, optional
        Default value if the environment variable is not set, default is None
    default_type : Optional[Union[Type, Callable]], optional
        Type conversion function or type, used to convert the string value to the expected type
        Can be built-in types (int, float, bool) or custom conversion functions
        
    Returns
    -------
    Any
        Converted environment variable value
        
    Examples
    --------
    >>> get_env_variable("DEBUG", "False", bool)
    False
    >>> get_env_variable("PORT", "8000", int)
    8000
    >>> get_env_variable("API_KEY", "")
    ''
    """
    # Get environment variable, if not set, use default value
    value = os.environ.get(name)
    
    # If environment variable is not set and default value is provided, use default value
    if value is None:
        return default
    
    # If no type conversion is specified, return the string value directly
    if default_type is None:
        return value
    
    # Try to convert type
    try:
        # Special handling for boolean values
        if default_type == bool and isinstance(value, str):
            return value.lower() in ('true', 'yes', 'y', '1', 'on')
        # Use provided type or function for conversion
        return default_type(value)
    except Exception as e:
        logging.warning(
            f"Failed to convert environment variable {name}='{value}' to {default_type.__name__} type: {str(e)}. "
            f"Using default value {default}"
        )
        return default