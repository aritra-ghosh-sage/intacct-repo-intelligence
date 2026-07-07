CREATE TABLE IF NOT EXISTS ui_companions (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER,
    kind TEXT,                 -- editor | lister | picker
    file_id INTEGER,
    language TEXT              -- javascript | typescript | xslt | phtml
);
