from datetime import datetime
import os
import logging
import threading
import time
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
    

class LogFormatter:
    _TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    _TIMESTAMP_CACHE_TIME = 0.5

    def __init__(self, name: str = "Logger", with_time: bool = True):
        self.name = name
        self.with_time = with_time
        self._last_timestamp = ""
        self._last_timestamp_time = 0
        self._timestamp_lock = threading.Lock()
        
    def _get_timestamp(self) -> str:
        """Get cached timestamp (thread-safe)"""
        current_time = time.time()
        if current_time - self._last_timestamp_time > self._TIMESTAMP_CACHE_TIME:
            with self._timestamp_lock:
                if current_time - self._last_timestamp_time > self._TIMESTAMP_CACHE_TIME:
                    self._last_timestamp = datetime.now().strftime(self._TIME_FORMAT)
                    self._last_timestamp_time = current_time
        return self._last_timestamp

    def _get_level_prefix(self, level: str) -> str:
        """Dynamic level prefix generation"""
        return f"{self.name} - {level} - " if not self.with_time else f" - {self.name} - {level} - "

    def format_message(self, msg: str, level: str) -> bytes:
        """Format log message as bytes"""
        prefix = self._get_level_prefix(level)
        timestamp = self._get_timestamp() if self.with_time else ""
        return f"{timestamp}{prefix}{msg}\n".encode('utf-8')
