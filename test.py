from ultralog import UltraLog
logger = UltraLog()

logger.info("test")

import loguru
loguru.logger.info("loguru test")