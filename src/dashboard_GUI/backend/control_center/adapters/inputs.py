"""Validated content-addressed CSV copies, never reference workload edits."""
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
from fastapi import HTTPException
from control_center.storage.files import open_source
from control_center.storage.jobs import owned_directory

MAX_INPUT=2*1024*1024

class InputStore:
    def __init__(self,data_dir):self.root=Path(data_dir)/'inputs'
    def resolve(self,input_id):
        if not re.fullmatch(r'[a-f0-9]{64}',input_id):raise HTTPException(422,'Invalid input ID')
        with open_source(self.root,input_id+'.csv') as (h,sig):
            if sig[2]>MAX_INPUT:raise HTTPException(413,'CSV exceeds 2 MiB')
            raw=h.read()
        if hashlib.sha256(raw).hexdigest()!=input_id:raise HTTPException(409,'Immutable CSV hash changed; upload the original file again')
        return self.root/(input_id+'.csv')
    def add(self,text,scenario_id='W100'):
        from mrta_core.orders import REQUIRED_COLUMNS,load_orders_csv
        from control_center.adapters.catalogue import load_scenario
        raw=text.encode('utf-8')
        if len(raw)>MAX_INPUT:raise HTTPException(413,'CSV exceeds 2 MiB')
        try:
            reader=csv.reader(io.StringIO(text,newline=''));header=next(reader)
            if header!=list(REQUIRED_COLUMNS):raise ValueError('CSV requires exactly order_id,item_id,shelf_id,quantity,dropoff_id in this order')
            count=0
            for row in reader:
                if len(row)!=5:raise ValueError('Every CSV row must have exactly five fields')
                count+=1
                if count>50000:raise ValueError('CSV exceeds 50000 line items')
            root=owned_directory(self.root)
            with tempfile.NamedTemporaryFile(dir=root,suffix='.csv',delete=False) as temp:temp.write(raw);path=Path(temp.name)
            try:
                _,warehouse=load_scenario(scenario_id);orders=load_orders_csv(path,warehouse)
                digest=hashlib.sha256(raw).hexdigest();target=root/(digest+'.csv')
                path.chmod(0o444)
                try:os.link(path,target,follow_symlinks=False)
                except FileExistsError:self.resolve(digest)
            finally:path.unlink(missing_ok=True)
        except (ValueError,UnicodeError,csv.Error,StopIteration) as exc:raise HTTPException(422,str(exc)) from exc
        return dict(id=digest,sha256=digest,orders=len(orders),line_items=count,bytes=len(raw),scenario_id=scenario_id)
