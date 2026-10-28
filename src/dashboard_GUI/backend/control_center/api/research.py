"""Read-only research analysis and explicit validated CSV ingestion."""
from fastapi import APIRouter,Depends,Query,Request
from pydantic import Field
from control_center.schemas.jobs import StrictModel
from control_center.schemas.responses import Envelope,result
from control_center.api.jobs import mutation
from control_center.api.routes import index,source_call
from control_center.adapters.experiments import ExperimentReader
from control_center.adapters.inputs import InputStore

router=APIRouter(prefix='/api/v1')

class InputRequest(StrictModel):
    csv_text:str=Field(min_length=1,max_length=2*1024*1024)

@router.post('/inputs',response_model=Envelope,dependencies=[Depends(mutation)])
def upload(request:Request,body:InputRequest):
    return result(source_call(lambda:InputStore(request.app.state.data_dir).add(body.csv_text)))

@router.get('/experiments',response_model=Envelope)
def experiments(request:Request):
    return result(source_call(lambda:ExperimentReader(index(request).root).list()))

@router.get('/experiments/{experiment_id}',response_model=Envelope)
def experiment(request:Request,experiment_id:str):
    value=source_call(lambda:ExperimentReader(index(request).root).detail(experiment_id))
    value.pop('rows',None)
    return result(value)

@router.get('/experiments/{experiment_id}/comparisons',response_model=Envelope)
def comparisons(request:Request,experiment_id:str,baseline:str=Query('nearest_astar',max_length=100),allow_incomplete:bool=False):
    return result(source_call(lambda:ExperimentReader(index(request).root).compare(experiment_id,baseline,allow_incomplete)))
