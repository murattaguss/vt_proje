createdb toolshare
psql -d toolshare -f schema.sql
pip install -r requirements.txt
python app.py