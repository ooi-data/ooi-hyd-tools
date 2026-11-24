FROM prefecthq/prefect:2-python3.11

COPY ./ /tmp/ooi-hyd-tools

RUN pip install uv
RUN uv pip install --system prefect-aws "/tmp/ooi-hyd-tools[pipeline]"

# day-rollup reads the cals baked into the image; ECS flows clone main and use its copy
ENV OOI_HYD_META_DIR=/tmp/ooi-hyd-tools/metadata
