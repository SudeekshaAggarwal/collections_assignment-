# Input data

Place the supplied CSV files from `collections30k` in this directory before running the pipeline. The repository intentionally does not duplicate the assignment's raw data.

Run the pipeline with:

`python golden/build_golden_dataset.py --data-dir data --output-dir golden --reports-dir reports`

The notebook assumes the same `data/` and `reports/` paths.
