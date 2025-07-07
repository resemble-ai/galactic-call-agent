# metrics_csv_logger.py
import csv
import os
from datetime import datetime
from typing import Dict, Any, Optional, TYPE_CHECKING
import threading
import logging
import traceback
import queue

# Create a logger for this module
metrics_logger = logging.getLogger("metrics_csv_logger")

if TYPE_CHECKING:
    from livekit.agents.metrics.base import (
        LLMMetrics,
        TTSMetrics,
        STTMetrics,
        VADMetrics,
        EOUMetrics,
    )
else:
    try:
        from livekit.agents.metrics.base import (
            LLMMetrics,
            TTSMetrics,
            STTMetrics,
            VADMetrics,
            EOUMetrics,
        )
    except ImportError as e:
        metrics_logger.error(f"Failed to import LiveKit metrics classes: {e}")

        # Define dummy classes to prevent errors
        class LLMMetrics:
            pass

        class TTSMetrics:
            pass

        class STTMetrics:
            pass

        class VADMetrics:
            pass

        class EOUMetrics:
            pass


class MetricsCSVLogger:
    def __init__(self, base_dir: str = "metrics"):
        self.base_dir = base_dir
        self.turn_counter = 0
        self.current_speech_id = None
        self.speech_id_to_turn = {}  # Map speech_id to turn number
        self.lock = threading.Lock()

        # Queue for non-blocking writes
        self.write_queue = queue.Queue(maxsize=1000)
        self.writer_thread = None
        self.stop_event = threading.Event()
        self.current_filename = None

        # Statistics
        self.write_count = 0
        self.error_count = 0
        self.dropped_count = 0

        try:
            # Create base directory if it doesn't exist
            os.makedirs(base_dir, exist_ok=True)
        except Exception as e:
            metrics_logger.error(f"Failed to create metrics directory {base_dir}: {e}")

    def get_csv_filename(self, llm_provider: str, model_name: str) -> str:
        """Generate CSV filename with provider and timestamp"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return os.path.join(
                self.base_dir, f"metrics_{llm_provider}_{model_name}_{timestamp}.csv"
            )
        except Exception as e:
            metrics_logger.error(f"Failed to generate CSV filename: {e}")
            return os.path.join(self.base_dir, "metrics_fallback.csv")

    def initialize_csv(self, filename: str) -> bool:
        """Initialize CSV with headers and start writer thread"""
        try:
            self.current_filename = filename

            # Simple headers - just 4 columns
            headers = [
                "metrics_type",
                "metrics_duration",
                "metrics_timestamp",
                "metrics_turncount",
            ]

            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()

            # Start the writer thread
            self.stop_event.clear()
            self.writer_thread = threading.Thread(
                target=self._writer_loop,
                args=(filename,),
                daemon=True,
                name="MetricsCSVWriter",
            )
            self.writer_thread.start()

            metrics_logger.info(f"Initialized CSV file: {filename}")
            return True

        except Exception as e:
            metrics_logger.error(f"Failed to initialize CSV {filename}: {e}")
            return False

    def _writer_loop(self, filename: str):
        """Background thread that writes metrics from queue to file"""
        metrics_logger.info(f"Starting CSV writer thread for {filename}")

        try:
            with open(filename, "a", newline="", buffering=1) as f:  # Line buffering
                headers = [
                    "metrics_type",
                    "metrics_duration",
                    "metrics_timestamp",
                    "metrics_turncount",
                ]
                writer = csv.DictWriter(f, fieldnames=headers)

                while not self.stop_event.is_set() or not self.write_queue.empty():
                    try:
                        # Get row with timeout
                        row = self.write_queue.get(timeout=0.1)
                        if row is None:  # Poison pill
                            break

                        # Write the row
                        writer.writerow(row)
                        f.flush()  # Ensure data is written

                        self.write_count += 1

                        # Log progress every 10 writes
                        if self.write_count % 10 == 0:
                            metrics_logger.debug(
                                f"Written {self.write_count} metrics to CSV"
                            )

                    except queue.Empty:
                        continue
                    except Exception as e:
                        self.error_count += 1
                        metrics_logger.error(f"Error writing row to CSV: {e}")

        except Exception as e:
            metrics_logger.error(
                f"Fatal error in writer thread: {e}\n{traceback.format_exc()}"
            )
        finally:
            metrics_logger.info(
                f"CSV writer thread stopped. Wrote {self.write_count} metrics, {self.error_count} errors"
            )

    def update_turn(self, speech_id: str) -> int:
        """Update turn counter when a new speech_id is encountered and return turn number"""
        try:
            with self.lock:
                if speech_id and speech_id not in self.speech_id_to_turn:
                    self.turn_counter += 1
                    self.speech_id_to_turn[speech_id] = self.turn_counter
                    self.current_speech_id = speech_id
                    metrics_logger.debug(
                        f"New turn {self.turn_counter} for speech_id: {speech_id}"
                    )

                return self.speech_id_to_turn.get(speech_id, self.turn_counter)
        except Exception as e:
            metrics_logger.error(f"Failed to update turn: {e}")
            return self.turn_counter

    def get_metric_type_name(self, metrics: Any) -> str:
        """Get the metric type name - simplified"""
        class_name = type(metrics).__name__

        # Simple mapping
        if isinstance(metrics, STTMetrics):
            return "STT"
        elif isinstance(metrics, LLMMetrics):
            return "LLM"
        elif isinstance(metrics, TTSMetrics):
            return "TTS"
        elif isinstance(metrics, VADMetrics):
            return "VAD"
        elif isinstance(metrics, EOUMetrics):
            return "EOU"
        else:
            return class_name

    def get_duration(self, metrics: Any) -> Optional[float]:
        """Extract duration from any metric type"""
        try:
            # All metrics types have 'duration' attribute
            duration = getattr(metrics, "duration", None)
            if duration is not None:
                return float(duration)
            return None
        except Exception as e:
            metrics_logger.debug(f"Failed to get duration: {e}")
            return None

    def write_metrics(self, filename: str, metrics: Any):
        """Queue metrics for writing (non-blocking)"""
        try:
            # Skip VAD and EOU metrics if you don't want them
            if isinstance(metrics, (VADMetrics, EOUMetrics)):
                return

            # Only process STT, LLM, and TTS
            if not isinstance(metrics, (STTMetrics, LLMMetrics, TTSMetrics)):
                return

            # Get speech_id and turn number
            speech_id = getattr(metrics, "speech_id", None) or self.current_speech_id
            turn_number = (
                self.update_turn(speech_id) if speech_id else self.turn_counter
            )

            # Get metric type
            metric_type = self.get_metric_type_name(metrics)

            # Get duration
            duration = self.get_duration(metrics)

            # Get timestamp
            timestamp = getattr(metrics, "timestamp", datetime.now().timestamp())

            # Prepare simple row with just 4 columns
            row = {
                "metrics_type": metric_type,
                "metrics_duration": duration,
                "metrics_timestamp": timestamp,
                "metrics_turncount": turn_number,
            }

            # Queue the row for writing (non-blocking)
            try:
                self.write_queue.put_nowait(row)

                # Simple log
                metrics_logger.info(
                    f"Queued metric: type={metric_type}, duration={duration}, turn={turn_number}"
                )
            except queue.Full:
                self.dropped_count += 1
                metrics_logger.warning(
                    f"Metrics queue full, dropped metric. Total dropped: {self.dropped_count}"
                )

        except Exception as e:
            metrics_logger.error(
                f"Error processing metrics: {e}\n{traceback.format_exc()}"
            )

    def stop(self):
        """Stop the writer thread and ensure all data is written"""
        metrics_logger.info("Stopping metrics CSV logger...")

        # Signal stop
        self.stop_event.set()

        # Add poison pill to queue
        try:
            self.write_queue.put(None, timeout=1)
        except queue.Full:
            pass

        # Wait for thread to finish
        if self.writer_thread and self.writer_thread.is_alive():
            self.writer_thread.join(timeout=5)

        metrics_logger.info(
            f"Metrics CSV logger stopped. "
            f"Written: {self.write_count}, "
            f"Errors: {self.error_count}, "
            f"Dropped: {self.dropped_count}"
        )

    def get_turn_summary(self) -> Dict[str, Any]:
        """Get summary of metrics"""
        try:
            return {
                "total_turns": self.turn_counter,
                "stats": {
                    "written": self.write_count,
                    "errors": self.error_count,
                    "dropped": self.dropped_count,
                    "queued": self.write_queue.qsize(),
                },
            }
        except Exception as e:
            metrics_logger.error(f"Failed to get turn summary: {e}")
            return {"total_turns": 0, "error": str(e)}
