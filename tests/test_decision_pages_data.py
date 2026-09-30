def draft():
 return {'stakeholder_context':[{'stakeholder':'Runner','role':'Tracks habits','goals':['Quick capture'],'operating_environment':'Phone, often offline','source_turns':[0]}],
 'operational_scenarios':[{'scenario_id':'OS-1','name':'Log habit','trigger':'Habit completed','actors':['Runner'],'operational_flow':['Open app','Record points'],'success_outcome':'Points sync','exceptions':['Offline'],'source_turns':[0]}],
 'concept_options':[{'concept_id':'concept:native-app','name':'Native app','summary':'Installed app','advantages':['Native APIs'],'disadvantages':['Store release']},{'concept_id':'concept:pwa','name':'PWA','summary':'Installable web product','advantages':['One deployment'],'disadvantages':['Platform limits']}]}
