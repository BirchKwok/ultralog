from datetime import datetime
import os
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
        print(
            f"Failed to convert environment variable {name}='{value}' to {default_type.__name__} type: {str(e)}. "
            f"Using default value {default}"
        )
        return default
    

class LogFormatter:
    _TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
    _TIMESTAMP_CACHE_TIME = 0.5
    DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s:%(func)s:%(line)s - %(message)s"

    def __init__(self, name: str = "Logger", with_time: bool = True, fmt: Optional[str] = None):
        self._name = name
        self.with_time = with_time
        self.fmt = fmt or self.DEFAULT_FORMAT
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

    def format_message(self, msg: str, level: str) -> bytes:
        """Format log message as bytes using the format string"""
        import sys
        import os
        
        # Initialize default values
        line_no = 1
        filename = "__main__"
        caller_info = f"{filename}:<module>:{line_no}"
        
        try:
            # Get the current frame (much faster than getouterframes)
            frame = sys._getframe(1)  # Skip this frame
            
            # Skip frames from ultralog itself
            while frame:
                if (not frame.f_globals.get('__name__', '').startswith('ultralog') and 
                   not frame.f_code.co_filename.endswith('ultralog/__init__.py')):
                    # Found first non-ultralog frame
                    line_no = frame.f_lineno
                    # In Jupyter notebooks, use __main__ instead of numeric filename
                    if '__file__' not in frame.f_globals:
                        filename = "__main__"
                    else:
                        filename = os.path.basename(frame.f_code.co_filename)
                    caller_info = f"{filename}:<module>:{line_no}"
                    break
                frame = frame.f_back
                
        except Exception:
            pass  # Keep default values if anything goes wrong

        # Cache the split parts to avoid repeated string splitting
        module, func, line = caller_info.split(':')
        record = {
            'asctime': self._get_timestamp() if self.with_time else "",
            'levelname': level,
            'message': msg,
            'module': module,
            'func': func,
            'line': line
        }
        
        try:
            formatted = self.fmt % record
        except KeyError as e:
            formatted = f"Invalid log format placeholder: {e}. Using default format."
            formatted = self.DEFAULT_FORMAT % record
            
        return f"{formatted}\n".encode('utf-8')

    @property
    def name(self) -> str:
        """Get the logger name (read-only)"""
        return self._name

    def set_format(self, fmt: str) -> None:
        """Update the log format string dynamically"""
        with self._timestamp_lock:  # Use existing lock for thread safety
            self.fmt = fmt
