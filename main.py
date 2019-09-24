import sys, getopt
from logistic_regression import LogisticRegression
from recurrent_cnn import RecurrentConvolutionalNN
from rnn import RecurrentNN
from cnn import ConvolutionalNN
from lstm import LongShortTermMemory
from metrics import metrics_handler
import output_handler
import torch

def init(filename):
    output_handler.outputFileHandler = output_handler.OutputHandler(filename)
    metrics_handler.metricsHandler = metrics_handler.MetricsHandler()

def main(argv):
    modelName = ''
    modelPossibilities = {
        'logreg': LogisticRegression,
        'rcnn': RecurrentConvolutionalNN,
        'rnn': RecurrentNN,
        'cnn': ConvolutionalNN,
        'lstm': LongShortTermMemory
    }
    outputFile = None
    classifierType = None
    classifierTypePossibilities = {
        'longer': 'longer',
        'repeater': 'repeater',
        'normal': 'normal'
    }
    embedding = None
    embeddingPossibilities = {
        'ft': 'fasttext',
        'glv': 'glove',
        'w2v': 'word2vec'
    }

    try:
        opts, args = getopt.getopt(argv, 'hmote:', ['help', 'model=', 'output=', 'type=', 'embedding='])
    except getopt.GetoptError:
        print('usage: main.py -m <modelname> or main.py --model=<modelname>, where <modelname>: rnn, lstm, cnn, rcnn or logreg')
        sys.exit(2)
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            print('usage: main.py -m <modelname> or main.py --model=<modelname>, where <modelname>: rnn, lstm, cnn, rcnn or logreg')
            sys.exit()
        elif opt in ('-m', '--model'):
            modelName = arg
        elif opt in ('-o', '--output'):
            outputFile = arg
        elif opt in ('-t', '--type'):
            classifierType = arg
        elif opt in ('-e', '--embedding'):
            embedding = arg
    
    modelHandlerName = modelPossibilities.get(modelName, 'Invalid model')
    if modelHandlerName == 'Invalid model':
        print('Invalid model name. Type python main.py -h for help')
        sys.exit()
    
    init(outputFile)
    output_handler.outputFileHandler.write("Start log \n")

    numberOfEpochs = 10

    if classifierType == classifierTypePossibilities['longer']:
        numberOfEpochs = 20
        modelHandler = modelHandlerName(embeddingPossibilities[embedding])
        modelHandler.train(numberOfEpochs)
        metrics_handler.metricsHandler.reset()
        test_loss, test_acc = modelHandler.test()
        print(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%')
        output_handler.outputFileHandler.write(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%\n')

        output_handler.outputFileHandler.write(f'Test recall: {metrics_handler.metricsHandler.getRecall():.3f}%\n')
        output_handler.outputFileHandler.write(f'Test precision: {metrics_handler.metricsHandler.getPrecision():.3f}%\n')
    elif classifierType == classifierTypePossibilities['repeater']:
        results = []
        modelHandler = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        for i in range(numberOfEpochs):
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            modelHandler = modelHandlerName(embeddingPossibilities[embedding])
            modelHandler.train(numberOfEpochs)
            metrics_handler.metricsHandler.reset()
            test_loss, test_acc = modelHandler.test()
            print(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%')
            output_handler.outputFileHandler.write(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%\n')

            output_handler.outputFileHandler.write(f'Test recall: {metrics_handler.metricsHandler.getRecall():.3f}%\n')
            output_handler.outputFileHandler.write(f'Test precision: {metrics_handler.metricsHandler.getPrecision():.3f}%\n')
    else:
        modelHandler = modelHandlerName(embeddingPossibilities[embedding])
        modelHandler.train(numberOfEpochs)
        metrics_handler.metricsHandler.reset()
        test_loss, test_acc = modelHandler.test()
        print(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%')
        output_handler.outputFileHandler.write(f'Test Loss: {test_loss:.3f}, Test Acc: {test_acc:.2f}%\n')

        output_handler.outputFileHandler.write(f'Test recall: {metrics_handler.metricsHandler.getRecall():.3f}%\n')
        output_handler.outputFileHandler.write(f'Test precision: {metrics_handler.metricsHandler.getPrecision():.3f}%\n')

    output_handler.outputFileHandler.close()

if __name__ == '__main__':
    main(sys.argv[1:])