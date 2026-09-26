# Binary file handler sub-package.
# Individual handler modules (pdf, image, office, archive, database) are
# imported directly by worker.binary — do not import them here to avoid
# pulling in optional dependencies at package load time.
