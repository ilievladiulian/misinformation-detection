# FakeNewsClassifier

### Setting up

This project uses the following dependencies:    

- pytorch 0.4.1   
- torchtext 0.3.1     
- numpy 1.16.3   
- pymongo 3.7.2    


### Dataset

There is a tool in /dataset/save_dataset.py used to extract examples from /dataset/news_cleaned.csv and save them to MongoDB. To check this feature, you need to manually add the file news_cleaned.csv in the /dataset directory.


### Usage

To run the training on the rcnn:

    python main.py

To run the training on the logistic regression model:

    python linear_regression.py
    