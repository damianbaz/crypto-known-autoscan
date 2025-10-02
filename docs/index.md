# Crypto Known Autoscan

- 📄 **[Reporte más reciente](./latest.md)**
- 🧾 **[JSON estructurado](./latest.json)**

## Reportes anteriores
{% for file in site.static_files %}
{% if file.path contains '/report-' and file.extname == '.md' %}
- [{{ file.name }}]({{ file.path | relative_url }})
{% endif %}
{% endfor %}
