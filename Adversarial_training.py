# coding: utf-8
import argparse
import time
import math
import os
import torch
import torch.nn as nn
import torch.onnx
import numpy as np
import pickle
import gan.discriminator_model as discriminator
import gan.judge_model as judge
import gan.data as data
import pandas as pd

import env_settings
from metrics import metrics_handler
import output_handler
import dataset.gan_load_dataset as dataset

parser = argparse.ArgumentParser(description='PyTorch RNN/LSTM classification Model')
parser.add_argument('--data', type=str, default=os.getcwd()+'/ag_news_csv/',
                    help='location of the data corpus')
parser.add_argument('--model', type=str, default='LSTM',
                    help='type of recurrent net (RNN_TANH, RNN_RELU, LSTM, GRU)')
parser.add_argument('--emsize', type=int, default=256,
                    help='size of word embeddings')
parser.add_argument('--nhid', type=int, default=512,
                    help='number of hidden units per layer')
parser.add_argument('--nlayers', type=int, default=1,
                    help='number of layers')
parser.add_argument('--lr', type=float, default=0.001,
                    help='initial learning rate')
parser.add_argument('--reduce_rate', type=float, default=0.95,
                    help='learning rate reduce rate')
parser.add_argument('--clip', type=float, default=5.0,
                    help='gradient clipping')
parser.add_argument('--nclass', type=int, default=4,
                    help='number of class in classification')
parser.add_argument('--epochs', type=int, default=400,
                    help='upper epoch limit')
parser.add_argument('--batch_size', type=int, default=4, metavar='N',
                    help='batch size')
parser.add_argument('--bptt', type=int, default=35,
                    help='sequence length')
parser.add_argument('--dropout_em', type=float, default=0.5,
                    help='dropout applied to layers (0 = no dropout)')
parser.add_argument('--dropout_rnn', type=float, default=0,
                    help='dropout applied to layers (0 = no dropout)')
parser.add_argument('--dropout_cl', type=float, default=0,
                    help='dropout applied to layers (0 = no dropout)')
parser.add_argument('--tied', action='store_true',
                    help='tie the word embedding and softmax weights')
parser.add_argument('--seed', type=int, default=1111,
                    help='random seed')
parser.add_argument('--cuda', action='store_true',
                    help='use CUDA')
parser.add_argument('--log_interval', type=int, default=10, metavar='N',
                    help='report interval')
parser.add_argument('--number_per_class', type=int, default=1000,
                    help='location of the data corpus')
parser.add_argument('--onnx-export', type=str, default='',
                    help='path to export the final model in onnx format')
parser.add_argument('--save', type=str,
                    default=os.getcwd()+'/ag_adv_model/',
                    help='path to save the final model')
parser.add_argument('--pre_train', type=str,
                    default=os.getcwd()+'/ag_lm_model/',
                    help='path to save the final model')
parser.add_argument('--embedding', type=str,
                    default='glove_specific', help='embedding vectors to use')
parser.add_argument('--output_file', type=str,
                    default='~/fake-news-master/results/gan-glove_specific.txt', help='metrics output file')

args = parser.parse_args()

# create the directory to save model if the directory is not exist
if not os.path.exists(args.save):
    os.makedirs(args.save)
resume = args.save+'resume_checkpoint/'
if not os.path.exists(resume):
    os.makedirs(resume)
result_dir = args.save+'result/'
if not os.path.exists(result_dir):
    os.makedirs(result_dir)

# Set the random seed manually for reproducibility.
torch.manual_seed(args.seed)
if torch.cuda.is_available():
    if not args.cuda:
        print("WARNING: You have a CUDA device, so you should probably run with --cuda")

metrics_handler.metricsHandler = metrics_handler.MetricsHandler()
output_handler.outputFileHandler = output_handler.OutputHandler(args.output_file)

###############################################################################
# Build the model
###############################################################################
dis_learning_rate = args.lr
judge_learning_rate = args.lr

def cycle(iterable):
    while True:
        for x in iterable:
            yield x

ntokens, embedding_vectors, labeled_train_loader, unlabeled_train_loader, valid_loader, test_loader, labeled_data_length, unlabeled_data_length, valid_length, test_length = dataset.load(args.embedding, batch_size=args.batch_size)

labeled_train_loader = iter(cycle(labeled_train_loader))
unlabeled_train_loader = iter(cycle(unlabeled_train_loader))
valid_loader = iter(cycle(valid_loader))
test_loader = iter(cycle(test_loader))

discriminator = discriminator.RNNModel(args.model, ntokens, args.emsize, args.nhid,
                       args.nlayers, args.nclass, embedding_vectors, args.dropout_em, 
                       args.dropout_rnn, args.dropout_cl, args.tied).cuda(env_settings.CUDA_DEVICE)
judger = judge.RNNModel(args.model, ntokens, args.emsize, args.nhid,
                       args.nlayers, args.nclass, embedding_vectors, args.dropout_em, 
                       args.dropout_rnn, args.dropout_cl, args.tied).cuda(env_settings.CUDA_DEVICE)

criterion = nn.CrossEntropyLoss(reduction='none')
criterion_judge = nn.BCELoss()
dis_optimizer = torch.optim.Adam(discriminator.parameters(), lr=dis_learning_rate, weight_decay=0.0005)
dis_scheduler = torch.optim.lr_scheduler.StepLR(dis_optimizer, step_size=10, gamma=args.reduce_rate)

judge_optimizer = torch.optim.Adam(judger.parameters(), lr=judge_learning_rate, weight_decay=0.0005)
judge_scheduler = torch.optim.lr_scheduler.StepLR(judge_optimizer, step_size=5, gamma=args.reduce_rate)

###############################################################################
# Training code
###############################################################################


def one_hot_embedding(labels, num_classes):
    """Embedding labels to one-hot form.

    Args:
      labels: (LongTensor) class labels, sized [N,].
      num_classes: (int) number of classes.

    Returns:
      (tensor) encoded labels, sized [N, #classes].
    """
    y = torch.eye(num_classes)
    return y[labels]


def dis_pre_train_step():
    discriminator.train()
    lab_batch = next(labeled_train_loader)
    lab_token_seqs = lab_batch.content[0]
    lab_seq_lengths = np.array([len(seq) for seq in lab_token_seqs])
    labels = lab_batch.label
    lab_token_seqs = torch.from_numpy(np.transpose(lab_token_seqs.numpy())).cuda(env_settings.CUDA_DEVICE)
    labels = torch.from_numpy(np.transpose(labels.numpy())).cuda(env_settings.CUDA_DEVICE)
    num_lab_sample = lab_token_seqs.shape[1]
    lab_hidden = discriminator.init_hidden(num_lab_sample)
    lab_output = discriminator(lab_token_seqs, lab_hidden, lab_seq_lengths)
    lab_element_loss = criterion(lab_output, labels)
    lab_loss = torch.mean(lab_element_loss)
    # Before the backward pass, use the optimizer object to zero all of the
    # gradients for the variables it will update (which are the learnable
    # weights of the model). This is because by default, gradients are
    # accumulated in buffers( i.e, not overwritten) whenever .backward()
    # is called.
    dis_optimizer.zero_grad()

    lab_loss.backward()

    # `clip_grad_norm` helps prevent the exploding gradient problem in RNNs / LSTMs.
    torch.nn.utils.clip_grad_norm_(discriminator.parameters(), args.clip)
    dis_optimizer.step()

    return lab_loss

def repackage_hidden(h):
    """Wraps hidden states in new Tensors, to detach them from their history."""
    if isinstance(h, torch.Tensor):
        return h.detach()
    else:
        return tuple(repackage_hidden(v) for v in h)

def adv_train_step(judge_only=True):
    discriminator.train()
    judger.train()

    # {token_seqs, next_token_seqs, importance_seqs, labels, seq_lengths, pad_length}
    # Sample m labeled instances from DL
    lab_batch = next(labeled_train_loader)
    lab_token_seqs = lab_batch.content[0]
    lab_seq_lengths = np.array([len(seq) for seq in lab_token_seqs])
    labels = lab_batch.label
    lab_token_seqs = torch.from_numpy(np.transpose(lab_token_seqs.numpy())).cuda(env_settings.CUDA_DEVICE)
    labels = torch.from_numpy(np.transpose(labels.numpy())).cuda(env_settings.CUDA_DEVICE)
    num_lab_sample = lab_token_seqs.shape[1]
    
    # Sample m labeled instances from DU and predict their corresponding label
    unl_batch = next(unlabeled_train_loader)
    unl_token_seqs = unl_batch.content[0]
    unl_seq_lengths = np.array([len(seq) for seq in unl_token_seqs])
    unl_token_seqs = torch.from_numpy(np.transpose(unl_token_seqs.numpy())).cuda(env_settings.CUDA_DEVICE)
    num_unl_sample = unl_token_seqs.shape[1]
    unl_hidden = discriminator.init_hidden(num_unl_sample)
    unl_output = discriminator(unl_token_seqs, unl_hidden, unl_seq_lengths)
    _, fake_labels = torch.max(unl_output, 1)

    if judge_only:
        k = 1
    else:
        k = 3

    for _k in range(k):
        # Update the judge model
        ###############################################################################
        lab_judge_hidden = judger.init_hidden(num_lab_sample)
        one_hot_label = one_hot_embedding(labels, args.nclass).cuda(env_settings.CUDA_DEVICE)  # one hot encoder
        lab_judge_prob = judger(lab_token_seqs, lab_judge_hidden, lab_seq_lengths, one_hot_label)
        lab_labeled = torch.ones(num_lab_sample).cuda(env_settings.CUDA_DEVICE)

        unl_judge_hidden = judger.init_hidden(num_unl_sample)
        one_hot_unl = one_hot_embedding(fake_labels, args.nclass).cuda(env_settings.CUDA_DEVICE)  # one hot encoder
        unl_judge_prob = judger(unl_token_seqs, unl_judge_hidden, unl_seq_lengths, one_hot_unl)
        unl_labeled = torch.zeros(num_unl_sample).cuda(env_settings.CUDA_DEVICE)
        
        if_labeled = torch.cat((lab_labeled, unl_labeled))
        all_judge_prob = torch.cat((lab_judge_prob, unl_judge_prob))
        all_judge_prob = all_judge_prob.view(-1)
        judge_loss = criterion_judge(all_judge_prob, if_labeled)
        judge_optimizer.zero_grad()

        judge_loss.backward()

        # `clip_grad_norm` helps prevent the exploding gradient problem in RNNs / LSTMs.
        torch.nn.utils.clip_grad_norm_(judger.parameters(), args.clip)
        judge_optimizer.step()

        unl_loss_value = 0.0
        lab_loss_value = 0.0
        fake_labels = repackage_hidden(fake_labels)
        unl_judge_prob = repackage_hidden(unl_judge_prob)
        if not judge_only:
            # Update the predictor
            ###############################################################################
            lab_hidden = discriminator.init_hidden(num_lab_sample)
            lab_output = discriminator(lab_token_seqs, lab_hidden, lab_seq_lengths)
            lab_element_loss = criterion(lab_output, labels)
            lab_loss = torch.mean(lab_element_loss)

            # calculate loss for unlabeled instances
            unl_hidden = discriminator.init_hidden(num_unl_sample)
            unl_output = discriminator(unl_token_seqs, unl_hidden, unl_seq_lengths)
            unl_element_loss = criterion(unl_output, fake_labels)
            unl_loss = unl_element_loss.dot(unl_judge_prob.view(-1))/num_unl_sample
            # do not include this in version 1 
            if _k<int(k/2):
                lab_unl_loss = lab_loss+unl_loss
            else:
                lab_unl_loss = unl_loss
            dis_optimizer.zero_grad()
            lab_unl_loss.backward()
            # `clip_grad_norm` helps prevent the exploding gradient problem in RNNs / LSTMs.
            torch.nn.utils.clip_grad_norm_(discriminator.parameters(), args.clip)
            dis_optimizer.step()
            
            unl_loss_value = unl_loss.item()
            lab_loss_value = lab_loss.item()

    return judge_loss, unl_loss_value, lab_loss_value


###############################################################################
# Training process
###############################################################################

def train(epoch=None, phase=None):
    # 1. pre_train discriminator.
    if phase == 'discriminator_only':#30
        num_iter = labeled_data_length // args.batch_size
        start_time = time.time()
        total_loss = 0
        for i_iter in range(num_iter):
            dis_loss = dis_pre_train_step()
            total_loss += dis_loss.item()
        elapsed = time.time() - start_time
        cur_loss = total_loss/num_iter
        print('Pre_train discriminator labeled_data only | epoch {:3d} | ms/batch {:5.2f} | '
              'labeled loss {:5.4f} | ppl {:8.4f}'.format(
            epoch, elapsed * 1000 / args.log_interval, cur_loss, math.exp(cur_loss)))
    # 2. pre_train judger and adv train.
    else:
        judge_scheduler.step()
        if phase == 'judge_only':#35
            judge_only = True
            current_process = 'Pre_train judger: '
        else:
            judge_only = False
            current_process = 'Adv train: '
        num_iter = unlabeled_data_length // args.batch_size
        start_time = time.time()
        total_judge_loss = 0
        total_unl_loss = 0
        total_lab_loss = 0
        for i_iter in range(num_iter):
            judge_loss, unl_loss_value, lab_loss_value = adv_train_step(judge_only=judge_only)
            total_judge_loss += judge_loss.item()
            total_unl_loss += unl_loss_value
            total_lab_loss += lab_loss_value

            if i_iter % args.log_interval == 0 and i_iter > 0:
                cur_judge_loss = total_judge_loss / args.log_interval
                cur_unl_loss = total_unl_loss / args.log_interval
                cur_lab_loss = total_lab_loss / args.log_interval
                elapsed = time.time() - start_time
                print(current_process+'| epoch {:3d} | {:5d}/{:5d} batches | ms/batch {:5.2f} | '
                      'judge_loss {:5.4f} | unlabel_loss {:5.4f} |label_loss {:5.4f}'.format(
                    epoch, i_iter, num_iter, elapsed * 1000 / args.log_interval, cur_judge_loss,
                    cur_unl_loss, cur_lab_loss))
                total_judge_loss = 0
                total_unl_loss = 0
                total_lab_loss = 0
                start_time = time.time()

###############################################################################
# Evaluate code
###############################################################################


def evaluate(test=False):
    # Turn on evaluate mode which disables dropout.
    correct = 0
    total = 0
    discriminator.eval()
    current_loader = valid_loader
    current_length = valid_length
    if test:
        current_loader = test_loader
        current_length = test_length
    with torch.no_grad():
        for i_batch in range(current_length):
            sample_batched = next(current_loader)
            token_seqs = sample_batched.content[0]
            seq_lengths = np.array([len(seq) for seq in token_seqs])
            labels = sample_batched.label
            token_seqs = torch.from_numpy(np.transpose(token_seqs.numpy())).cuda(env_settings.CUDA_DEVICE)
            labels = torch.from_numpy(np.transpose(labels.numpy())).cuda(env_settings.CUDA_DEVICE)
            hidden = discriminator.init_hidden(token_seqs.shape[1])
            output = discriminator(token_seqs, hidden, seq_lengths)
            _, predict_class = torch.max(output,1)
            total += labels.size(0)
            correct += (predict_class == labels).sum().item()

            for i_metric in range(list(predict_class.size())[0]):
                metrics_handler.metricsHandler.update((predict_class.data)[i_metric].item(), (labels.data)[i_metric].item())
        test_acc = 100 * correct / total
        print('Accuracy of the classifier on the test data is : {:5.4f}'.format(test_acc))

        if test:
            output_handler.outputFileHandler.write(f'Test Acc: {test_acc:.2f}%\n')
            output_handler.outputFileHandler.write(f'Test recall: {metrics_handler.metricsHandler.getRecall():.3f}%\n')
            output_handler.outputFileHandler.write(f'Test precision: {metrics_handler.metricsHandler.getPrecision():.3f}%\n')
        else:
            output_handler.outputFileHandler.write(f'Valid Acc: {test_acc:.2f}%\n')
            output_handler.outputFileHandler.write(f'Valid recall: {metrics_handler.metricsHandler.getRecall():.3f}%\n')
            output_handler.outputFileHandler.write(f'Valid precision: {metrics_handler.metricsHandler.getPrecision():.3f}%\n')
        return correct / total


###############################################################################
# The learning process
###############################################################################
epoch = 0
dis_resume_file = os.path.join(resume, 'discriminator_checkpoint.pth.tar')
judge_resume_file = os.path.join(resume, 'judger_checkpoint.pth.tar')
pre_trained_lm_model_file = os.path.join(args.pre_train, 'lm_model.pt')
result_file = os.path.join(result_dir, 'result.csv')

if os.path.isfile(result_file):
    all_result_df = pd.read_csv(result_file)
else:
    all_result_df = pd.DataFrame(columns=['batch', 'accuracy'])

###############################################################################
# check if there is a chekpoint for resuming or there is a
# pretrained language model to update model
try:
    # first check if there is a checkpoint for resuming
    if os.path.isfile(dis_resume_file) and os.path.isfile(judge_resume_file):
        print("=> loading discriminator's checkpoint from '{}'".format(dis_resume_file))
        dis_checkpoint = torch.load(dis_resume_file)
        start_epoch = dis_checkpoint['epoch']
        dis_scheduler = dis_checkpoint['scheduler']
        discriminator.load_state_dict(dis_checkpoint['model_state_dict'])
        dis_optimizer.load_state_dict(dis_checkpoint['optimizer'])

        print("=> loading judger's checkpoint from '{}'".format(judge_resume_file))
        judge_checkpoint = torch.load(judge_resume_file)
        judge_scheduler = judge_checkpoint['scheduler']
        judger.load_state_dict(judge_checkpoint['model_state_dict'])
        judge_optimizer.load_state_dict(judge_checkpoint['optimizer'])

        print("=> loaded discriminator's checkpoint '{}' and judger's checkpoint '{}' (epoch {})"
              .format(dis_resume_file, judge_resume_file, dis_checkpoint['epoch']))
    else:
        # if no checkpoint for resuming then check if there is a pre_trained language model
        print("=> no checkpoint found at '{}'".format(dis_resume_file))
        print("=> no checkpoint found at '{}'".format(judge_resume_file))
        print("Now check if there is a pre_trained language model")
        if os.path.isfile(pre_trained_lm_model_file):
            print("=> Initialize the classification model with '{}'".
                  format(pre_trained_lm_model_file))
            pre_trained_lm_model = torch.load(pre_trained_lm_model_file)
            discriminator.load_state_dict(pre_trained_lm_model.state_dict(), strict=False)
            judger.load_state_dict(pre_trained_lm_model.state_dict(), strict=False)
        else:
            print("=> No pretrained language model can be found at '{}'".
                  format(pre_trained_lm_model_file))
        start_epoch = 1

###############################################################################
# this is the training loop and each loop run a batch
    best_accuracy = 0
    patience_threshold = 3
    patience = patience_threshold
    phase = 'discriminator_only'
    for epoch in range(start_epoch, args.epochs + 1):
        if epoch == 30 and phase == 'discriminator_only':
            phase = 'judge_only'
        if epoch == 50 and phase == 'judge_only':
            phase = 'adversarial_training'
        metrics_handler.metricsHandler.reset()
        epoch_start_time = time.time()
        dis_scheduler.step()
        train(epoch=epoch, phase=phase)
        current_accuracy = evaluate()
        cdf = pd.DataFrame([[epoch, current_accuracy]], columns=['batch', 'accuracy'])
        all_result_df = all_result_df.append(cdf, ignore_index=True)

        patience -= 1
        # Save the model if the validation loss is the best we've seen so far.
        if current_accuracy > best_accuracy and abs(current_accuracy - best_accuracy) > 0.001:
            best_accuracy = current_accuracy
            with open(os.path.join(args.save, 'discriminator.pt'), 'wb') as f:
                torch.save(discriminator, f)
            with open(os.path.join(args.save, 'discriminator-optimizer.pt'), 'wb') as f:
                torch.save(dis_optimizer, f)
            with open(os.path.join(args.save, 'judger.pt'), 'wb') as f:
                torch.save(judger, f)
            with open(os.path.join(args.save, 'judger-optimizer.pt'), 'wb') as f:
                torch.save(judge_optimizer, f)
            patience = patience_threshold
        
        if patience == 0:
            if phase == 'discriminator_only':
                discriminator = torch.load(os.path.join(args.save, 'discriminator.pt'))
                dis_optimizer = torch.load(os.path.join(args.save, 'discriminator-optimizer.pt'))
                phase = 'judge_only'
                patience = patience_threshold
            elif phase == 'judge_only':
                judge = torch.load(os.path.join(args.save, 'judger.pt'))
                judge_optimizer = torch.load(os.path.join(args.save, 'judger-optimizer.pt'))
                phase = 'adversarial_training'
                patience = patience_threshold
            else:
                break

    metrics_handler.metricsHandler.reset()
    discriminator = torch.load(os.path.join(args.save, 'discriminator.pt'))
    judge = torch.load(os.path.join(args.save, 'judger.pt'))
    evaluate(test=True)
###############################################################################
# save the result and the final checkpoint
    all_result_df.to_csv(result_file, index=False, header=True)

    torch.save(
        {'epoch': epoch,
         'model_state_dict': discriminator.state_dict(),
         'scheduler': dis_scheduler,
         'optimizer': dis_optimizer.state_dict()
         }, dis_resume_file)
    torch.save(
        {'model_state_dict': judger.state_dict(),
         'scheduler': judge_scheduler,
         'optimizer': judge_optimizer.state_dict()
         }, judge_resume_file)

    print('-' * 89)
    print("save the check point to '{}' and '{}'".
          format(dis_resume_file, judge_resume_file))

###############################################################################
# At any point you can hit Ctrl + C to break out of training early.
except KeyboardInterrupt:
    print('-' * 89)
    print("Exiting from training early")
    print("save the check point to '{}' and '{}'".
          format(dis_resume_file, judge_resume_file))
    torch.save(
        {'epoch': epoch,
         'model_state_dict': discriminator.state_dict(),
         'scheduler': dis_scheduler,
         'optimizer': dis_optimizer.state_dict()
         }, dis_resume_file)
    torch.save(
        {'model_state_dict': judger.state_dict(),
         'scheduler': judge_scheduler,
         'optimizer': judge_optimizer.state_dict()
         }, judge_resume_file)
    print("save the current result to '{}'".format(result_file))
    all_result_df.to_csv(result_file, index=False, header=True)

print('=' * 89)
print('End of training and evaluation')
