# Training a Neural CLBF


## Setup:
```
docker compose build
docker compose up &
docker exec -it <container name> bash
```


## Running (inside the docker container):

make a directory to store the trained data:
```
mkdir -p src/train/data
```

Now run the training
```
cd src/train
python3 Crazyflie_train_new.py
```

Change the `batch_size` parameter in this file to max out your GPU.


