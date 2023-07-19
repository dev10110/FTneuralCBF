FROM anibali/pytorch:2.0.0-cuda11.8


SHELL ["/bin/bash", "-c"]


RUN sudo apt-get update && sudo apt-get install -y vim


RUN python3 -m pip install pytictoc matplotlib osqp qpsolvers pytorch_lightning


