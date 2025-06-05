import sys
from pathlib import Path
import asyncio

# --- Path Hack for direct execution ---
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# --- End Path Hack ---

import redis # Main redis module for exceptions
import redis.asyncio as aioredis
import json
from typing import Callable, Any, Coroutine

from config.app_config import app_config
from common.logger_setup import setup_logger

logger = setup_logger(__name__, level_str=app_config.LOG_LEVEL)

class RedisClient:
    def __init__(self):
        try:
            self.sync_redis_client = redis.Redis(
                host=app_config.REDIS_HOST,
                port=app_config.REDIS_PORT,
                db=app_config.REDIS_DB,
                password=app_config.REDIS_PASSWORD,
                decode_responses=True
            )
            self.sync_redis_client.ping()
            logger.info(f"Synchronous Redis client connected to {app_config.REDIS_HOST}:{app_config.REDIS_PORT}")
        except redis.exceptions.ConnectionError as e: # Use redis.exceptions
            logger.error(f"Failed to connect synchronous Redis client: {e}")
            self.sync_redis_client = None

        self.async_redis_client = None

    async def _get_async_redis_client(self):
        # For async client, we primarily rely on successful ping or operations.
        # Connection check is implicit in operations.
        if self.async_redis_client is None: # Initialize if None
            try:
                self.async_redis_client = aioredis.Redis(
                    host=app_config.REDIS_HOST,
                    port=app_config.REDIS_PORT,
                    db=app_config.REDIS_DB,
                    password=app_config.REDIS_PASSWORD,
                    decode_responses=True
                )
                await self.async_redis_client.ping() # Test connection on creation
                logger.info(f"Asynchronous Redis client (re)connected to {app_config.REDIS_HOST}:{app_config.REDIS_PORT}")
            except (redis.exceptions.ConnectionError, Exception) as e: # Use redis.exceptions
                logger.error(f"Failed to connect asynchronous Redis client: {e}")
                if self.async_redis_client: # Ensure it's None if connection failed
                    await self.async_redis_client.aclose() # Use aclose
                    self.async_redis_client = None
                raise
        # To check if an existing client is still connected, we can try a ping before returning it.
        # However, this adds overhead. Usually, operations will fail if not connected.
        # For robustness, especially if the client might have disconnected:
        elif self.async_redis_client is not None:
             try:
                 await self.async_redis_client.ping()
             except redis.exceptions.ConnectionError:
                 logger.warning("Async Redis client lost connection. Attempting to reconnect.")
                 await self.async_redis_client.aclose() # Use aclose
                 self.async_redis_client = None
                 # Call self again to re-initialize
                 return await self._get_async_redis_client()
             except Exception as e_ping: # Other errors during ping
                 logger.error(f"Error pinging existing async Redis client: {e_ping}")
                 # Decide if to force reconnect or raise
                 await self.async_redis_client.aclose() # Use aclose
                 self.async_redis_client = None
                 return await self._get_async_redis_client()


        return self.async_redis_client

    async def publish_command(self, channel: str, command_data: dict) -> bool:
        if not isinstance(command_data, dict):
            logger.error(f"Command data must be a dictionary. Received: {type(command_data)}")
            return False
        try:
            client = await self._get_async_redis_client()
            if client:
                message_json = json.dumps(command_data)
                await client.publish(channel, message_json)
                logger.debug(f"Published to {channel}: {message_json}")
                return True
            logger.warning("Cannot publish command, async Redis client not available.")
            return False
        except redis.exceptions.ConnectionError: # Specific catch for connection issues
            logger.error(f"Connection error publishing to Redis channel {channel}. Forcing client re-init on next call.")
            if self.async_redis_client:
                await self.async_redis_client.aclose() # Use aclose
            self.async_redis_client = None
            return False
        except Exception as e:
            logger.error(f"Error publishing to Redis channel {channel}: {e}")
            return False

    async def subscribe_to_channel(self, channel_pattern: str,
                                   callback: Callable[[str, dict], Coroutine[Any, Any, None]]):
        pubsub = None
        client = None
        while True:
            try:
                client = await self._get_async_redis_client()
                if not client:
                    logger.error("Cannot subscribe, async Redis client not available. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                pubsub = client.pubsub()
                await pubsub.psubscribe(channel_pattern)
                logger.info(f"Subscribed to Redis channel pattern: {channel_pattern}")

                async for message in pubsub.listen():
                    if message and message["type"] == "pmessage":
                        try:
                            actual_channel = message["channel"]
                            message_data_dict = json.loads(message["data"])
                            logger.debug(f"Received from {actual_channel} (matched by {channel_pattern}): {message_data_dict}")
                            asyncio.create_task(callback(actual_channel, message_data_dict))
                        except json.JSONDecodeError as e:
                            logger.error(f"Error decoding JSON from Redis message on {message['channel']}: {e} - Data: {message['data']}")
                        except Exception as e:
                            logger.error(f"Error in Redis message callback for {message['channel']}: {e}")
            except redis.exceptions.ConnectionError: # Use redis.exceptions
                logger.warning(f"Redis connection lost during subscription to {channel_pattern}. Reconnecting in 5s...")
                if client:
                    await client.aclose() # Use aclose
                self.async_redis_client = None
                client = None
                if pubsub:
                    # No explicit close for pubsub object itself, it's tied to client connection
                    pubsub = None
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info(f"Subscription to {channel_pattern} cancelled.")
                if pubsub and client: # Ensure client exists before trying to use pubsub methods
                    try:
                        # Check if client is still valid before unsubscribe
                        # No direct is_connected, rely on it not throwing if connection is bad during this critical cleanup
                        if client: # Redundant check but safe
                           await pubsub.unsubscribe(channel_pattern) #This might fail if connection is truly gone
                    except Exception as e_unsub:
                        logger.error(f"Error during pubsub.unsubscribe on cancellation: {e_unsub}")
                    # pubsub object itself doesn't have a separate close/aclose. It's managed by the client.
                break
            except Exception as e:
                logger.error(f"Unexpected error in Redis subscription loop for {channel_pattern}: {e}")
                await asyncio.sleep(10)


    async def close_async_client(self):
        if self.async_redis_client:
            try:
                await self.async_redis_client.aclose() # Use aclose
                logger.info("Asynchronous Redis client connection closed.")
            except Exception as e:
                logger.error(f"Error closing async_redis_client: {e}")
            finally:
                self.async_redis_client = None


# --- Test Functions (no changes needed here for these specific errors) ---
async def test_subscriber_callback(channel: str, data: dict):
    print(f"\n[TEST CALLBACK] Received on actual channel '{channel}': {data}")
    logger.info(f"[TEST CALLBACK] Received on actual channel '{channel}': {data}")

async def test_redis_pub_sub():
    redis_client = RedisClient()

    if redis_client.sync_redis_client:
        try:
            ping_response = redis_client.sync_redis_client.ping()
            print(f"Sync Redis Ping: {ping_response}")
            logger.info(f"Sync Redis Ping successful: {ping_response}")
        except Exception as e:
            print(f"Sync Redis Ping failed: {e}")
            logger.error(f"Sync Redis Ping failed: {e}")
            return
    else:
        print("Sync Redis client not connected.")
        logger.warning("Sync Redis client not connected for test.")
        return

    try:
        await redis_client._get_async_redis_client()
        if not redis_client.async_redis_client:
            print("Async Redis client not connected after attempt.")
            logger.warning("Async Redis client not connected after attempt for test.")
            return
    except Exception as e:
        print(f"Could not connect async Redis client for test: {e}")
        logger.error(f"Could not connect async Redis client for test: {e}")
        return

    test_channel_pattern = "test:commands:*"
    specific_test_channel = "test:commands:call123"

    logger.info(f"\nStarting subscriber for pattern: {test_channel_pattern}")
    subscriber_task = asyncio.create_task(
        redis_client.subscribe_to_channel(test_channel_pattern, test_subscriber_callback)
    )

    await asyncio.sleep(1) # Give subscriber time to connect

    logger.info(f"\nPublishing test messages to {specific_test_channel}...")
    command1 = {"action": "play_audio", "file": "welcome.wav"}
    command2 = {"user_id": 101, "details": {"item": "test_item"}}

    pub1_ok = await redis_client.publish_command(specific_test_channel, command1)
    logger.info(f"Publish command1 status: {pub1_ok}")
    pub2_ok = await redis_client.publish_command(specific_test_channel, command2)
    logger.info(f"Publish command2 status: {pub2_ok}")
    await redis_client.publish_command("test:other_events", {"event": "system_shutdown"}) # Should not be caught

    await asyncio.sleep(2) # Allow time for messages to be processed

    logger.info("\nCancelling subscriber task...")
    subscriber_task.cancel()
    try:
        await subscriber_task
    except asyncio.CancelledError:
        logger.info("Subscriber task successfully cancelled.")
    except Exception as e_task_await:
        logger.error(f"Exception awaiting cancelled subscriber_task: {e_task_await}")

    await redis_client.close_async_client()
    logger.info("Test finished.")


if __name__ == "__main__":
    print("Running Redis Client Test from __main__...")
    try:
        asyncio.run(test_redis_pub_sub())
    except Exception as e:
        print(f"Error running test_redis_pub_sub from __main__: {e}")
        import traceback
        traceback.print_exc()