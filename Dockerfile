FROM prefecthq/prefect:2-python3.11

COPY ./ /tmp/ooi-hyd-tools

ENV HDF5_USE_FILE_LOCKING=FALSE
RUN pip install prefect-aws
RUN pip install -e /tmp/ooi-hyd-tools
