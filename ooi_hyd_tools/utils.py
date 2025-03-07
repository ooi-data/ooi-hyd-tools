
def select_logger():
    from prefect import get_run_logger
    try:
        logger = get_run_logger()
    except Exception as e:
        print(e)
        from loguru import logger
    
    return logger