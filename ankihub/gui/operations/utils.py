from concurrent.futures import Future


def future_with_exception(e: BaseException) -> Future:
    future: Future = Future()
    future.set_exception(e)
    return future


def future_with_result(result: object) -> Future:
    future: Future = Future()
    future.set_result(result)
    return future
