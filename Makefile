FILE ?= murik.txt
TABLE ?= murik-en
LANG ?= en
DB := storage/knowledge.db

.PHONY: help parse synk csv ui desktop db

help:
	@echo "make parse FILE=$(FILE) LANG=$(LANG)"
	@echo "make synk TABLE=$(TABLE)"
	@echo "make csv TABLE=$(TABLE)"
	@echo "make ui"
	@echo "make desktop"
	@echo "make db"

parse:
	python -u main.py --parse $(FILE) $(LANG)

synk:
	python -u main.py --synk $(TABLE)

csv:
	python -u main.py --create-csv $(TABLE)

ui:
	python -u main.py --ui

desktop:
	python -u main.py --desktop

db:
	python -m sqlite3 $(DB)
