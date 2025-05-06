from fastapi import FastAPI
from pydantic import BaseModel
...
return {"status": f"Ziel {update.id} auf '{update.status}' gesetzt"}