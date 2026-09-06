# The HTTP front end as a Lambda image.
#
#   docker build -f deploy/Dockerfile -t tradeval .      (from the repo root)
#
# An image rather than a zip because the dependency set is pandas, numpy and
# lxml: around 200MB unpacked, which fits a zip's 250MB limit with little room
# and none of the convenience. The image limit is 10GB, and ECR charges about
# ten cents a gigabyte-month to hold it.
#
# arm64 on purpose. Lambda's Graviton pricing is a fifth cheaper per
# gigabyte-second than x86 for the same work, and every wheel this needs
# publishes an aarch64 build.
FROM public.ecr.aws/lambda/python:3.12-arm64

# Dependencies first, and by themselves: this layer is the expensive one, and
# it should not rebuild because a check changed.
COPY deploy/requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-lambda.txt

# The service, and the checklist it is a front end over. validate.py and
# trade.sh are deliberately absent -- they are the other front end, and Lambda
# has no terminal to draw one in.
COPY serve.py ${LAMBDA_TASK_ROOT}/
COPY tradeval ${LAMBDA_TASK_ROOT}/tradeval
COPY deploy/lambda_handler.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
