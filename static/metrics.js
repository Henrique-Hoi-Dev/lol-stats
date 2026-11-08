// Compartilhado entre / e /partida: elos e métricas comparadas com as bases de referência.
const TIERS = ['IRON','BRONZE','SILVER','GOLD','PLATINUM','EMERALD','DIAMOND','MASTER','GRANDMASTER','CHALLENGER'];
const TIER_PT = {IRON:'Ferro', BRONZE:'Bronze', SILVER:'Prata', GOLD:'Ouro', PLATINUM:'Platina', EMERALD:'Esmeralda',
  DIAMOND:'Diamante', MASTER:'Mestre', GRANDMASTER:'Grão-Mestre', CHALLENGER:'Desafiante', ELITE:'Top BR'};


// higher: maior é melhor · skip: rotas onde a métrica não faz sentido
// tip(você, meta, quem) — "quem" = "a média do Platina", "a elite"...
const BM = [
  {k:'kda', n:'KDA', d:2, higher:true,
   tip:(y,e,w)=>`<b>KDA:</b> <b>${y}</b>; ${w} tem <b>${e}</b>. Morrer menos vale mais do que abater mais: lute quando o seu time estiver perto e com vantagem numérica.`},
  {k:'csMin', n:'CS/min', d:2, higher:true, skip:['Suporte'],
   tip:(y,e,w)=>`<b>Farm:</b> você faz <b>${y}</b> CS/min; ${w} faz <b>${e}</b>. Não deixe minions morrerem na rota, pegue waves laterais no meio do jogo e farme no caminho entre um objetivo e outro.`},
  {k:'cs10', n:'CS aos 10 min', d:0, higher:true, skip:['Suporte','Selva'],
   tip:(y,e,w)=>`<b>Fase de rotas:</b> <b>${y}</b> minions aos 10 min; ${w} faz <b>${e}</b>. Treine last hit no modo prática e não troque dano quando uma wave grande estiver morrendo.`},
  {k:'goldMin', n:'Ouro/min', d:0, higher:true,
   tip:(y,e,w)=>`<b>Renda:</b> <b>${y}</b> de ouro/min; ${w} faz <b>${e}</b>. Some farm, placas e objetivos: toda volta à base deveria render um item ou componente importante.`},
  {k:'deaths', n:'Mortes', d:1, higher:false,
   tip:(y,e,w)=>`<b>Mortes:</b> <b>${y}</b> por jogo; ${w} morre <b>${e}</b>. Antes de avançar, pergunte onde estão o caçador e o meio inimigo; sem visão do lado de lá, jogue recuado.`},
  {k:'kp', n:'Part. em abates %', d:0, higher:true,
   tip:(y,e,w)=>`<b>Presença no mapa:</b> participação em abates de <b>${y}%</b>; ${w} tem <b>${e}%</b>. Olhe o minimapa a cada poucos segundos e chegue antes nas lutas por dragão, arauto e torres.`},
  {k:'dmgMin', n:'Dano/min', d:0, higher:true,
   tip:(y,e,w)=>`<b>Dano:</b> <b>${y}</b> de dano/min; ${w} causa <b>${e}</b>. Procure trocas mais frequentes na rota e, nas lutas, fique em posição de bater o tempo todo sem se expor.`},
  {k:'dmgShare', n:'% dano do time', d:1, higher:true,
   tip:(y,e,w)=>`<b>Impacto nas lutas:</b> você causa <b>${y}%</b> do dano do time; ${w} causa <b>${e}%</b>. Esteja presente nas lutas e acerte suas habilidades principais.`},
  {k:'visionMin', n:'Visão/min', d:2, higher:true,
   tip:(y,e,w)=>`<b>Visão:</b> placar de visão de <b>${y}</b>/min; ${w} tem <b>${e}</b>. Use o trinket sempre que estiver carregado e coloque sentinelas antes dos objetivos.`},
  {k:'controlWards', n:'Sentinelas de controle', d:1, higher:true,
   tip:(y,e,w)=>`<b>Sentinelas de controle:</b> <b>${y}</b> por jogo; ${w} compra <b>${e}</b>. Compre pelo menos uma a cada volta à base, é o item mais barato com maior impacto.`},
  {k:'wardsKilled', n:'Sentinelas destruídas', d:1, higher:true,
   tip:(y,e,w)=>`<b>Negar visão:</b> você destrói <b>${y}</b> sentinelas por jogo; ${w} destrói <b>${e}</b>. Use a Lente do Oráculo antes de dragão e barão.`},
  {k:'turrets', n:'Torres (participação)', d:1, higher:true,
   tip:(y,e,w)=>`<b>Objetivos:</b> você participa de <b>${y}</b> torres por jogo; ${w} participa de <b>${e}</b>. Converta abates e vantagens em placas e torres em vez de voltar para a base sem pressionar.`},
  {k:'soloKills', n:'Abates solo', d:1, higher:true, skip:['Suporte'],
   tip:(y,e,w)=>`<b>Agressividade na rota:</b> <b>${y}</b> abates solo por jogo; ${w} faz <b>${e}</b>. Aprenda os níveis de poder do seu campeão (nível 2, 3 e 6) e puna os erros do inimigo.`},
];
