FROM prefecthq/prefect:2-python3.11

COPY ./ /tmp/ooi-hyd-tools

RUN pip install uv
RUN uv pip install --system prefect-aws /tmp/ooi-hyd-tools
