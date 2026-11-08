# LoL Stats

Digite um Riot ID (`Nome#TAG`) e o sistema baixa da Riot API tudo o que dá:
conta, nível, elo (Solo/Duo e Flex), maestrias e o histórico de partidas
(até 1000), e mostra gráficos e estatísticas.

## Como rodar

1. Pegue uma chave em https://developer.riotgames.com (a chave de dev expira em 24h).
2. Instale e rode:

```bash
pip install -r requirements.txt
export RIOT_API_KEY=RGAPI-sua-chave   # Windows PowerShell: $env:RIOT_API_KEY="RGAPI-..."
python app.py
```

3. Abra http://localhost:5000

## O que aparece

- Elo, PDL, winrate da temporada, maestrias
- KPIs: winrate, KDA, KP%, CS/min, dano/min, ouro/min, visão/min, multikills
- Winrate ao longo do tempo (média móvel + acumulado)
- Winrate por campeão, rota, fila, lado do mapa, horário, dia da semana e duração do jogo
- Comparação das suas médias em vitórias × derrotas
- Tendência de KDA, CS/min e dano por jogo
- Duos frequentes, confrontos de rota (contra quais campeões você perde), histórico completo
- Insights automáticos (melhor/pior campeão, horário, efeito "tilt" após derrota...)
- Filtros por fila, campeão, rota e últimas N partidas; exportação CSV

## Notas

- Chave de dev: 100 requisições a cada 2 min → ~100 partidas novas a cada 2 min.
  Partidas ficam em `cache/` e nas próximas buscas carregam na hora.
- Remakes são excluídos das estatísticas.
- A Riot só guarda o histórico de partidas dos últimos ~2 anos.
