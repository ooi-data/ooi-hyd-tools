import os

def select_logger():
    from prefect import get_run_logger
    try:
        logger = get_run_logger()
    except Exception as e:
        print(e)
        from loguru import logger
    
    return logger


def get_s3_kwargs():
    aws_key = os.environ.get("AWS_KEY")
    aws_secret = os.environ.get("AWS_SECRET")
    
    s3_kwargs = {'key': aws_key, 'secret': aws_secret}
    return s3_kwargs