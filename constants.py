SUPPORTED_REPORT_TYPES = ["NPA", "SOA", "TrialBalance", "CashBook", "BalanceSheet", "PAndL"]

REPORT_CONFIGS_DIR = "report_configs"
REPORT_OUTPUTS_DIR = "report_outputs"
REPORT_OUTPUTS_DIR = "report_outputs"
EXPORTS_DIR = "exports"

QDRANT_STORAGE_PATH = "./qdrant_storage"
QDRANT_COLLECTION_PREFIX = "lumio_"
GEMINI_EMBEDDING_MODEL = "models/text-embedding-004"
VECTOR_SIZE = 768

MAX_ROWS_PER_PAGE = 30
SAMPLE_ROWS = 500
DEBOUNCE_MS = 500

COLUMN_OPERATIONS = ["Sum", "Average", "Count Distinct", "Group By", "Min", "Max", "Distribution", "Display Only"]

GEMINI_MODEL         = "gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.88
CONFIDENCE_THRESHOLD = 0.80
CI_DEBUG             = os.getenv("CI_DEBUG", "false").lower() == "true"


REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT      = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD  = os.getenv("REDIS_PASSWORD", None)
REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", 1800))