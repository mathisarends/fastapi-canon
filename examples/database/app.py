from abc import ABC, abstractmethod

from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from examples.database.config import database
from examples.database.schema import ItemRow
from fastapi_canon import APIRouter, Composition, Feature


class Item(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ItemRepository(ABC):
    @abstractmethod
    async def add(self, name: str) -> None: ...


class SqlItemRepository(ItemRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, name: str) -> None:
        self._session.add(ItemRow(name=name))
        await self._session.flush()


class ItemService:
    def __init__(self, item_repository: ItemRepository) -> None:
        self._item_repository = item_repository

    async def create(self, item: Item) -> Item:
        await self._item_repository.add(item.name)
        return item


class ItemProvider(Provider):
    scope = Scope.REQUEST
    item_repository = provide(SqlItemRepository, provides=ItemRepository)
    item_service = provide(ItemService)


router = APIRouter(prefix="/items", route_class=DishkaRoute)


@router.post("", status_code=201)
async def create_item(item: Item, item_service: FromDishka[ItemService]) -> Item:
    return await item_service.create(item)


app = Composition(
    features=[
        database.feature,
        Feature(name="items", routers=[router], providers=[ItemProvider]),
    ],
).build(FastAPI())
