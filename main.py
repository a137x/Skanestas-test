from random import random
from shutil import move
from threading import Thread
import time
from typing import Any, Dict, List, Optional, Set
import logging 
import sys
from enum import Enum
import json
import asyncio


from pydantic import BaseModel, ValidationError
from loguru import logger
from loguru._defaults import LOGURU_FORMAT
from fastapi import FastAPI, Query, Header, WebSocket, Body, Request, Form
from starlette.endpoints import WebSocketEndpoint
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import janus

class InterceptHandler(logging.Handler):
    """
    Default handler from examples in loguru documentaion.
    See https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def format_record(record: dict) -> str:
    format_string = LOGURU_FORMAT

    if record["extra"].get("payload") is not None:
        record["extra"]["payload"] = pformat(
            record["extra"]["payload"], indent=4, compact=True, width=88
        )
        format_string += "\n<level>{extra[payload]}</level>"

    format_string += "{exception}\n"
    return format_string


app = FastAPI(title="API server", debug=True)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# set loguru format for root logger
logging.getLogger().handlers = [InterceptHandler()]

# set format
logger.configure(
    handlers=[{"sink": sys.stdout, "level": logging.DEBUG, "format": format_record}]
)

# init tickers dictionary; no persistent storage :-(
tickers = dict()
for _ in range(100):
    _ = str(_).zfill(2)
    tickers[f'{_}'] = list()


@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    Thread(target=ticker_generator, args=(loop, )).start()
    

@app.on_event("shutdown")
async def shutdown_event():
    logger.debug('Shuting down server. Disconnecting db and cancelling tasks.')
    # app.task.cancel()


def generate_movement():
    movement = -1 if random() < 0.5 else 1
    return movement


def ticker_generator(loop):
    # pass
    while True:
        future = asyncio.run_coroutine_threadsafe(coro(int(time.time())), loop)
        time.sleep(1)


async def coro(timestamp):
    for _ in range(100):
        _ = str(_).zfill(2) # normalizing ticker number, adding leading zero if needed
        if len(tickers[_]) == 0:
            previous_value = 0
            tickers[_].append({'time': timestamp - 1,'price':0})
        else:
            previous_value = tickers[_][-1]['price']            
        new_value = previous_value + generate_movement() 
        tickers[_].append({'time': timestamp,'price': new_value})
        ws_list = ws_queues.get_ws_list(_)
        for socket in ws_list: # NOTE: this loop is blocking, TODO: async (Aiter)
            await ws_queues.put_to_q(socket, _, [_,[{'time': timestamp,'price': new_value}]])
    
    
@app.get("/")
def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket_route("/ws")
class WSEndpoint(WebSocketEndpoint):
    async def on_connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        logger.debug('Websocket connected.')

    async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:
        logger.debug('Websocket disconnected.')

    async def on_receive(self, websocket: WebSocket, data: Any):
        logger.debug(data)
        ticker_dict = dict()
        for ticker in tickers:
            ticker_dict[ticker] = ticker
            
        AvailableTicker = Enum('AvailableTicker', ticker_dict)

        class SubscriptionCommand(str, Enum):
            subscribe = "subscribe"
            unsubscribe = "unsubscribe"

        class Subscription(BaseModel):
            command: SubscriptionCommand 
            ticker: AvailableTicker
    
        try:
            data = json.loads(data)
            Subscription(
                command=data['command'],
                ticker=data['ticker']
            )
            if data['command'] == 'subscribe' and not ws_queues.ws_in_channel(websocket, data['ticker']):
                ws_queues.add_queue(websocket, data['ticker'])
                await websocket.send_json([data['ticker'], tickers[data['ticker']]])
                await ws_queues.start_emmiter(websocket, data['ticker'])

            elif data['command'] == 'unsubscribe' and ws_queues.ws_in_channel(websocket, data['ticker']):
                """Unsubscribing from channel"""
                await ws_queues.stop_emmiter(websocket, data['ticker'])

        except ValidationError as e:
            e = str(e).split("\n")
            await websocket.send_json({"error": "in field '%s'%s" % (e[1], e[2])})
        except TypeError as e:
            logger.error(e)
            await websocket.send_json({"error": "wrong data format"})
        except json.decoder.JSONDecodeError as e:
            await websocket.send_json({"error": "sent data is not a valid JSON"})
        except KeyError as e:
            await websocket.send_json({"error": "%s field is missing" % (e)})


class WSQ:
    """ Object that holds all WebSocket Queues """
    def __init__(self):
        self.queues: Dict[str, Dict[str, janus.Queue()]] = {}
        """ {"<ws_channel>": {str(websocket): Queue,...},...} """
        self.q_index: Dict[str, Set[WebSocket]] = {}
        """ Index of queues ^, shows what websockets are in channel: {"<ws_channel>": {websocket_1, websocket_2,...},...} """
        self.emmiters: Dict[str, asyncio.Task] = {}
        """ {"str(websocket)" + "<ws_channel>": asyncio.Task()}"""

    def add_queue(self, websocket: WebSocket, ws_channel: str):
        if ws_channel not in self.queues:
            self.queues[ws_channel] = {}
        self.queues[ws_channel][str(websocket)] = janus.Queue().async_q
        if ws_channel not in self.q_index:
            self.q_index[ws_channel] = set()
        self.q_index[ws_channel].add(str(websocket))

    def remove_queue(self, websocket: WebSocket, ws_channel: str):
        """ Removing queue and clean up index"""
        try:
            del self.queues[ws_channel][str(websocket)]
            self.q_index[ws_channel].discard(str(websocket))
        except Exception as e:
            logger.debug(e)
            pass
    
    def ws_in_channel(self, websocket: WebSocket, ws_channel: str) -> bool:
        if ws_channel in self.q_index:
            if str(websocket) in self.q_index[ws_channel]:
                logger.debug('return true')
                return True
            else:
                logger.debug('return false')
                return False
        else:
            logger.debug('return false')
            return False

    async def put_to_q(self, websocket: WebSocket, ws_channel: str, message):
        """ Putting message to queue """
        try:
            await self.queues[ws_channel][str(websocket)].put(message)
        except:
            pass # in case of absence of websocket in channel (i.e. unsubscribed)

    async def get_from_q(self, websocket: WebSocket, ws_channel):
        """ Remove and return an item from the queue. If queue is empty, wait until an item is available."""
        return await self.queues[ws_channel][str(websocket)].get()

    def get_ws_list(self, ws_channel):
        """ Retunrs a list of Websocket objects"""
        if ws_channel in self.q_index:
            return self.q_index[ws_channel]
        else:
            return set()
    
    async def start_emmiter(self, websocket: WebSocket, ws_channel: str):
        '''Starting emmiter an saving its task in 'emmiters' dict. '''
        try:
            self.emmiters[str(websocket)+str(ws_channel)].cancel() # precaution -- canceling existng emmiter, if any
        except Exception as e:
            logger.debug(e)
            pass
        self.emmiters[str(websocket)+str(ws_channel)] = asyncio.create_task(self.ws_emmiter(websocket, ws_channel))
    
    async def stop_emmiter(self, websocket: WebSocket, ws_channel: str):
        '''Stopping emmiter and removing its task from 'emmiters' dict. '''
        try:
            self.emmiters[str(websocket)+ws_channel].cancel()
        except:
            pass
        try:
            del self.emmiters[str(websocket)+ws_channel]
        except KeyError:
            pass
        self.remove_queue(websocket, ws_channel) 
        logger.debug(f'{str(websocket)+ws_channel} emmiter has stopped!')

    async def ws_emmiter(self, websocket: WebSocket, ws_channel: str):
        """ Intended to run only form class methods! """
        while True:
            update = await self.get_from_q(websocket, ws_channel)
            await websocket.send_json(update)
            self.queues[ws_channel][str(websocket)].task_done()
            await asyncio.sleep(0)
 
ws_queues = WSQ()
