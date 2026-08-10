from app.features.tasks.infrastructure.provider import TaskProvider
from app.features.tasks.presentation.errors import register_exception_handlers
from app.features.tasks.presentation.router import router
from app.presentation.feature import Feature

feature = Feature(
    name="tasks",
    routers=(router,),
    providers=(TaskProvider,),
    register_exception_handlers=register_exception_handlers,
)
