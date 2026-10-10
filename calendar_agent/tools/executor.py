from pydantic import Field
from .calendar import SCHEMAS, Arguments, execute
from .weather import Forecast, Suggest
from ..memory.retrieval import retrieve


class Search(Arguments):
    query: str = Field(min_length=1, max_length=2000)


class Executor:
    def __init__(self, factory, clock, memory, weather):
        self.factory, self.clock, self.memory, self.weather = factory, clock, memory, weather

    def schemas(self):
        schemas = dict(SCHEMAS) | {'get_weather': Forecast, 'create_adjustment_suggestion': Suggest, 'search_memory': Search}
        return {k: v.model_json_schema() for k, v in schemas.items()}

    def execute(self, tool, arguments, request_id, expected_version):
        if tool in SCHEMAS:
            result = execute(self.factory, tool, arguments, request_id, expected_version, self.clock())
            if tool != 'read_calendar':
                from ..memory.tasks import sync
                # A memory failure must not misreport an already committed calendar write.
                try:
                    sync(self.memory, result)
                except ValueError:
                    result = result | {'memory_status': 'not_stored'}
            return result
        if tool == 'get_weather':
            payload = Forecast.model_validate(arguments)
            return self.weather.provider.forecast(payload.location, payload.day)
        if tool == 'create_adjustment_suggestion':
            payload = Suggest.model_validate(arguments)
            return self.weather.suggest(payload.session_id, payload.location)
        if tool == 'search_memory':
            payload = Search.model_validate(arguments)
            mode = getattr(self.memory, 'retrieval_mode', 'hybrid')
            return [] if mode == 'none' else retrieve(self.memory, payload.query, mode=mode)
        raise PermissionError('未注册的工具')
