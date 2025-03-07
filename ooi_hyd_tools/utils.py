
def select_logger():
    from prefect import get_run_logger
    try:
        logger = get_run_logger()
    except:
        from loguru import logger
    
    return logger