with Arthexis.ORM;

package Apps.App.Models.App_Models is
   --  Schema objects owned by the App backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create app matrix tables and indexes.
end Apps.App.Models.App_Models;
