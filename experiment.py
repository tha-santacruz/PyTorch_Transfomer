import torch
import torch.nn as nn
import torch.nn.functional as f
import torch.cuda.amp as amp
from tqdm import tqdm
from torch.utils.data import DataLoader

from dataset import OpusTranslationDataset
from transformer import TransformerModel

class Experiment():
    def __init__(self, cfg):
        self.cfg = cfg
        self.dataset = OpusTranslationDataset(
            dataset_name=self.cfg.DATASET_NAME,
            language_source=self.cfg.LANGUAGE_INPUT,
            language_target=self.cfg.LANGUAGE_TARGET,
            sequence_length=self.cfg.SEQUENCE_LENGTH,
            vocab_size=self.cfg.VOCAB_SIZE
        )
        self.net = TransformerModel(
            d_model=self.cfg.MODEL_DIMS, 
            h=self.cfg.MODEL_HEADS, 
            l=self.cfg.MODEL_LAYERS, 
            num_tokens=self.cfg.VOCAB_SIZE,
        ).to(device=self.cfg.DEVICE)
        if self.cfg.LOAD_CHECKPOINT:
            self.net.load_state_dict(torch.load(f"checkpoints/{self.cfg.LOAD_CHECKPOINT}.pt"))
        
        self.default_dtype = torch.float16 if self.cfg.HALF_BITS else torch.float32

        for param in self.net.parameters():
            param.data = param.data.to(dtype=self.default_dtype).clone().detach()
            if param.grad is not None:
                param.grad.data = param.grad.data.to(dtype=self.default_dtype).clone().detach()


        self.optimizer = torch.optim.Adam(
            params=self.net.parameters(),
            lr=self.cfg.LEARNING_RATE,
            betas=(0.9, 0.98),
            eps=1e-9,
        )
        self.criterion = nn.CrossEntropyLoss(label_smoothing=0.1).to(device=self.cfg.DEVICE)
        self.scaler = amp.GradScaler()

    def train_round(self):
        self.dataset.use_set("train")
        loader = DataLoader(
            dataset=self.dataset,
            batch_size=self.cfg.BATCH_SIZE,
            shuffle=True,
            prefetch_factor=1,
            num_workers=self.cfg.NUM_WORKERS,
        )
        self.net.train()
        round_loss = 0.0
        round_acc = 0.0
        batch_num = 1
        pbar = tqdm(loader)


        for batch in pbar:
            self.optimizer.zero_grad()

            input_ids = batch[0].to(dtype=torch.int64, device=self.cfg.DEVICE)
            input_mask = batch[1].to(dtype=self.default_dtype, device=self.cfg.DEVICE)
            target_ids = batch[2].to(dtype=torch.int64, device=self.cfg.DEVICE)
            target_mask = batch[3].to(dtype=self.default_dtype, device=self.cfg.DEVICE)
            with amp.autocast():
                pred_probs, pred_ids = self.net(input_ids, input_mask)
            
            loss = self.criterion(pred_probs.view(-1, pred_probs.size(-1)), target_ids.view(-1))

            self.scaler.scale(loss).backward()

            nn.utils.clip_grad_norm(self.net.parameters(), 1.0)

            self.scaler.step(self.optimizer)
            self.scaler.update()
            
            with torch.no_grad():
                success = (pred_ids==target_ids)[target_mask==1]
                acc = success.sum()/success.size(-1)
            
            pbar.set_description(f"Last batch train loss and accuracy : {loss.item():.2f}, {acc.item():.2f}")
            round_loss += loss.item()
            round_acc += acc.item()
            batch_num += 1
        return round_loss/batch_num, round_acc/batch_num
    
    def eval_round(self):
        self.dataset.use_set("val")
        loader = DataLoader(
            dataset=self.dataset,
            batch_size=self.cfg.BATCH_SIZE,
            shuffle=True,
            prefetch_factor=1,
            num_workers=self.cfg.NUM_WORKERS,
        )
        self.net.eval()
        round_loss = 0.0
        round_acc = 0.0
        batch_num = 1
        pbar = tqdm(loader)
        for batch in pbar:
            input_ids = batch[0].to(dtype=torch.int64, device=self.cfg.DEVICE)
            input_mask = batch[1].to(dtype=self.default_dtype, device=self.cfg.DEVICE)
            target_ids = batch[2].to(dtype=torch.int64, device=self.cfg.DEVICE)
            target_mask = batch[3].to(dtype=self.default_dtype, device=self.cfg.DEVICE)
            with torch.no_grad():
                pred_probs, pred_ids = self.net(input_ids, input_mask)
                loss = self.criterion(pred_probs.view(-1, pred_probs.size(-1)), target_ids.view(-1))
                    
                success = (pred_ids==target_ids)[target_mask==1]
                acc = success.sum()/success.size(-1)
            
            pbar.set_description(f"Last batch val loss and accuracy : {loss.item():.2f}, {acc.item():.2f}")
            round_loss += loss.item()
            round_acc += acc.item()
            batch_num += 1
        return round_loss/batch_num, round_acc/batch_num

    def train(self):
        
        best_val_loss = 1e9

        for epoch in range(self.cfg.EPOCH_NUM):
            train_loss, train_acc = self.train_round()
            val_loss, val_acc = self.eval_round()
            
            if val_loss < best_val_loss:
                if self.cfg.SAVE_CHECKPOINTS:
                    torch.save(self.net.state_dict(), f"checkpoints/{self.cfg.SAVE_CHECKPOINTS}.pt")
                best_val_loss = val_loss

            print(f"Metrics after epoch {epoch+1} : train loss : {train_loss:.4f} | train accuracy : {train_acc:.4f} | validation loss : {val_loss:.4f} | validation accuracy {val_acc:.4f}")
            
        ## NEED GRAD SCALER, SMOOTHING, DTYPE setting for model and data

        
    
